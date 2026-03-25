import sqlite3
import os
import re
import sys
import json
import requests
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor # <--- 新增
import threading # <--- 新增
from deep_translator import GoogleTranslator

# --- 1. 基础路径管理 ---
def get_app_dir():
    if getattr(sys, 'frozen', False):
        path = sys.executable
        for _ in range(4): path = os.path.dirname(path)
        return path
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
HISTORY_FILE = os.path.join(APP_DIR, "exported_history.json")
CONFIG_FILE = os.path.join(APP_DIR, "anki_config.json")
DESKTOP_PATH = os.path.join(os.path.expanduser("~"), "Desktop")

# --- 2. 核心服务 (词典与翻译) ---
try:
    from DictionaryServices import DCSCopyTextDefinition
except ImportError:
    DCSCopyTextDefinition = None

def beautify_definition(text):
    if not text: return ""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    p_tones = "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ"
    text = re.sub(rf'\b(?=[a-zA-Z]*[{p_tones}])[a-zA-Z{p_tones}]+\b', '', text)
    text = re.sub(r'(?<!^)\b([A-Z]\.\s+)', r'<br><b>\1</b>', text)
    text = re.sub(r'(?<!^)([①-⑩\u2460-\u246b]|\b\d+\.)', r'<br><b>\1</b>', text)
    return f'<div class="dict-body">{text.replace("\n", "<br>")}</div>'

def get_macos_definition(word):
    if not DCSCopyTextDefinition: return ""
    word = word.strip()
    res = DCSCopyTextDefinition(None, word, (0, len(word)))
    return beautify_definition(res.strip()) if res else ""

def translate_text(text):
    try:
        clean_text = text.replace('\n', ' ').strip()
        if not clean_text: return ""
        return GoogleTranslator(source='auto', target='zh-CN').translate(clean_text)
    except:
        return "[翻译失败]"

# --- 3. AnkiConnect API ---
class AnkiAPI:
    @staticmethod
    def invoke(action, **params):
        try:
            response = requests.post('http://127.0.0.1:8765', json={
                'action': action, 'version': 6, 'params': params
            }, timeout=10).json()
            return response.get('result')
        except: return None

    @staticmethod
    def ensure_model():
        model_name = "Kindle_Eink_Model_V7"
        existing = AnkiAPI.invoke('modelNames')
        if existing and model_name in existing: return model_name
        fields = ["stem", "word", "usage", "usage_translation", "title", "authors", "creation_tm", "mdx_dict", "mdx_name"]
        css = """ .card { font-family: arial; font-size: 18px; color: black; background-color: white; padding: 10px; }
        .stem-box { text-align: center; margin-top: 20px; }
        .stem { font-size: 32px; font-weight: bold; color: black; display: inline-block; }
        .proto-wrap { font-size: 20px; color: #888; margin-left: 8px; font-weight: normal; }
        .usage { font-style: italic; color: #333; margin: 25px 15px 5px 15px; line-height: 1.5; text-align: left; }
        .usage b { color: black; text-decoration: underline; font-weight: bold; }
        .translation { font-size: 16px; color: #555; margin: 0 15px 15px 15px; text-align: left; border-left: 3px solid #eee; padding-left: 10px; }
        .definition { border-top: 1px solid #ccc; padding-top: 10px; font-size: 16px; margin: 10px; }
        .cover { text-align: center; margin-top: 15px; } .cover img { width: 10ch; border: 1px solid #eee; }
        .footer { font-size: 12px; color: #999; text-align: center; margin-top: 30px; } """
        front = """<div class="stem-box"><span class="stem">{{stem}}</span>{{#word}}<span class="proto-wrap">({{word}})</span>{{/word}}</div><div class="usage">{{usage}}</div>"""
        back = """{{FrontSide}}{{#usage_translation}}<div class="translation">{{usage_translation}}</div>{{/usage_translation}}<hr id="answer"><div class="definition">{{mdx_dict}}</div><div class="cover">{{mdx_name}}</div><div class="footer">{{title}} | {{authors}} | {{creation_tm}}</div>"""
        AnkiAPI.invoke('createModel', modelName=model_name, inOrderFields=fields, cardTemplates=[{'Name': 'Card 1', 'Front': front, 'Back': back}], css=css)
        return model_name

# --- 4. 主程序 ---
class KindleAnkiProV31:
    def __init__(self, window):
        self.window = window
        self.window.title("Kindle-Anki 异步全息版 V31")
        self.window.geometry("1300x800")
        
        self.exported_ids = self.load_history()
        self.config = self.load_config()
        self.all_data = []
        self.is_syncing = False # 防止重复点击
        
        self.setup_ui()
        self.auto_load_kindle()

    def load_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f: return set(json.load(f))
            except: return set()
        return set()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f: return json.load(f)
            except: pass
        return {"deck": "Default", "model": "Kindle_Eink_Model_V7"}

    def setup_ui(self):
        top_frame = ttk.LabelFrame(self.window, text=" 功能控制面板 ", padding=10)
        top_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(top_frame, text="牌组:").pack(side=tk.LEFT)
        self.deck_combo = ttk.Combobox(top_frame, width=15); self.deck_combo.pack(side=tk.LEFT, padx=5)
        ttk.Label(top_frame, text="模板:").pack(side=tk.LEFT)
        self.model_combo = ttk.Combobox(top_frame, width=20); self.model_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(top_frame, text="刷新 Anki", command=self.refresh_anki).pack(side=tk.LEFT, padx=10)
        self.sync_btn = ttk.Button(top_frame, text="⚡一键异步同步 (超快)", command=self.start_async_sync)
        self.sync_btn.pack(side=tk.LEFT, padx=10)

        self.nb = ttk.Notebook(self.window); self.nb.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
        self.tab_p = ttk.Frame(self.nb); self.tab_d = ttk.Frame(self.nb)
        self.nb.add(self.tab_p, text="  待处理单词  "); self.nb.add(self.tab_d, text="  已归档记录  ")
        self.tree_p = self.create_tree(self.tab_p); self.tree_d = self.create_tree(self.tab_d, is_done=True)

    def create_tree(self, parent, is_done=False):
        ctrl = ttk.Frame(parent, padding=5); ctrl.pack(side=tk.TOP, fill=tk.X)
        if not is_done:
            ttk.Button(ctrl, text="刷新 Kindle 数据", command=self.auto_load_kindle).pack(side=tk.LEFT, padx=5)
            ttk.Button(ctrl, text="全选所有", command=lambda: self.tree_p.selection_set(self.tree_p.get_children())).pack(side=tk.LEFT, padx=5)
        else:
            ttk.Button(ctrl, text="← 移回待处理", command=self.restore_word).pack(side=tk.LEFT, padx=5)
        
        cols = ("word", "stem", "usage", "book", "time", "id")
        tree = ttk.Treeview(parent, columns=cols, show='headings', selectmode="extended")
        for col in cols: tree.heading(col, text=col); tree.column(col, width=120)
        tree.column("id", width=0, stretch=tk.NO); tree.column("usage", width=400)
        tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        vsb = ttk.Scrollbar(parent, command=tree.yview); tree.configure(yscrollcommand=vsb.set); vsb.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    # --- 异步同步核心逻辑 ---
    def start_async_sync(self):
        if self.is_syncing: return
        
        deck = self.deck_combo.get()
        model = self.model_combo.get()
        selected = self.tree_p.selection()
        if not selected: selected = self.tree_p.get_children()
        if not selected: return

        # 1. 创建进度弹窗
        self.prog_win = tk.Toplevel(self.window)
        self.prog_win.title("同步任务进行中...")
        self.prog_win.geometry("400x150")
        self.prog_win.transient(self.window)
        self.prog_win.grab_set() # 模态窗
        
        self.prog_label = ttk.Label(self.prog_win, text="准备开始同步...", padding=10)
        self.prog_label.pack()
        self.progress = ttk.Progressbar(self.prog_win, length=300, mode='determinate')
        self.progress.pack(pady=10)
        self.progress["maximum"] = len(selected)
        
        # 2. 开启后台线程
        self.is_syncing = True
        self.sync_btn.config(state="disabled")
        threading.Thread(target=self.sync_worker, args=(selected, deck, model), daemon=True).start()

    def sync_worker(self, selected, deck, model):
        """后台工作线程"""
        success_count = 0
        total = len(selected)
        
        # 使用并发线程池处理翻译和发送 (5个并发)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i, item in enumerate(selected):
                l_id = str(self.tree_p.item(item)['values'][5])
                raw = next(r for r in self.all_data if str(r[7]) == l_id)
                futures.append(executor.submit(self.process_single_word, raw, deck, model))
            
            # 等待结果并更新进度条
            for i, future in enumerate(futures):
                if future.result(): success_count += 1
                # 通过 after 跨线程更新进度条
                self.window.after(0, self.update_progress, i + 1, total)

        # 任务结束，通知主线程
        self.window.after(0, self.sync_finished, success_count)

    def process_single_word(self, raw, deck, model):
        """单个单词的处理逻辑（在线程池中运行）"""
        try:
            word_clean = raw[0].split(':')[-1] if ':' in raw[0] else raw[0]
            raw_stem = raw[1] if (raw[1] and raw[1].strip()) else word_clean
            word_for_anki = "" if word_clean.lower() == raw_stem.lower() else raw_stem
            
            translation = translate_text(raw[2])
            definition = get_macos_definition(word_clean) or get_macos_definition(raw_stem)
            cover_html = f'<img src="https://images-na.ssl-images-amazon.com/images/P/{raw[6]}.01.LZZZZZZZ.jpg">' if raw[6] else ""
            time_str = datetime.fromtimestamp(raw[5]/1000).strftime('%Y-%m-%d %H:%M')

            note = {"deckName": deck, "modelName": model,
                    "fields": {
                        "stem": word_clean, "word": word_for_anki, 
                        "usage": raw[2].replace('â€™', "'").replace('â€”', "—"), 
                        "usage_translation": translation, "title": raw[3], 
                        "authors": raw[4], "creation_tm": time_str, 
                        "mdx_dict": definition, "mdx_name": cover_html
                    },
                    "options": {"allowDuplicate": False}, "tags": ["kindle_async_v31"]}
            
            res = AnkiAPI.invoke('addNote', note=note)
            if res:
                self.exported_ids.add(str(raw[7]))
                return True
        except: pass
        return False

    def update_progress(self, current, total):
        self.progress["value"] = current
        self.prog_label.config(text=f"正在同步: {current} / {total}")

    def sync_finished(self, count):
        self.is_syncing = False
        self.sync_btn.config(state="normal")
        self.prog_win.destroy()
        
        # 保存历史
        with open(HISTORY_FILE, 'w') as f: json.dump(list(self.exported_ids), f)
        self.refresh_ui()
        messagebox.showinfo("同步完成", f"⚡ 闪电同步成功：{count} 个单词已入库！")

    # --- 其他基础逻辑保持不变 ---
    def refresh_anki(self):
        AnkiAPI.ensure_model()
        decks = AnkiAPI.invoke('deckNames'); models = AnkiAPI.invoke('modelNames')
        if decks: self.deck_combo['values'] = decks
        if models: self.model_combo['values'] = models
        self.deck_combo.set(self.config.get("deck", "Default"))
        self.model_combo.set(self.config.get("model", "Kindle_Eink_Model_V7"))

    def auto_load_kindle(self):
        path = "/Volumes/Kindle/system/vocabulary/vocab.db"
        if not os.path.exists(path): path = filedialog.askopenfilename(title="选择 Kindle 数据库", filetypes=[("DB", "vocab.db")])
        if not path: return
        conn = sqlite3.connect(path)
        self.all_data = conn.execute("SELECT WORDS.word, WORDS.stem, LOOKUPS.usage, BOOK_INFO.title, "
                                     "BOOK_INFO.authors, LOOKUPS.timestamp, BOOK_INFO.asin, LOOKUPS.id "
                                     "FROM LOOKUPS JOIN WORDS ON LOOKUPS.word_key = WORDS.id "
                                     "JOIN BOOK_INFO ON LOOKUPS.book_key = BOOK_INFO.id ORDER BY LOOKUPS.timestamp DESC").fetchall()
        conn.close(); self.refresh_ui()

    def refresh_ui(self):
        for t in [self.tree_p, self.tree_d]:
            for item in t.get_children(): t.delete(item)
        for r in self.all_data:
            l_id = str(r[7]); word = r[0].split(':')[-1] if ':' in r[0] else r[0]
            target = self.tree_d if l_id in self.exported_ids else self.tree_p
            target.insert("", "end", values=(word, r[1], r[2].strip(), r[3], datetime.fromtimestamp(r[5]/1000).strftime('%y-%m-%d'), l_id))

    def restore_word(self):
        for item in self.tree_d.selection():
            l_id = str(self.tree_d.item(item)['values'][5])
            if l_id in self.exported_ids: self.exported_ids.remove(l_id)
        with open(HISTORY_FILE, 'w') as f: json.dump(list(self.exported_ids), f); self.refresh_ui()

if __name__ == "__main__":
    root = tk.Tk(); ttk.Style().theme_use('clam'); KindleAnkiProV31(root); root.mainloop()

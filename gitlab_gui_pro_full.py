"""
GitLab Desktop Client - gitlab_gui_pro_full.py

Mô tả: Ứng dụng GUI bằng tkinter (Tiếng Việt) để tương tác với GitLab instance (mặc định git.rikkei.edu.vn)
Tính năng chính:
 - Quản lý Access Token (keyring hoặc fallback file base64)
 - Treeview phân cấp: Group -> Subgroup -> Project -> Repository -> File (lazy-load)
 - Tạo Group/Subgroup/Project qua popup
 - Upload file / folder (folder nén zip và upload) + commit text files (tùy chọn)
 - Xem file text/code với syntax highlight (pygments nếu có)
 - Lưu log, export log
 - Tìm kiếm nhanh (search) và mở node chứa kết quả
 - Dark mode toggle (ttkbootstrap nếu có, fallback style)
 - Threading cho các thao tác mạng và upload, progress bar cho upload nhiều file
 - Lưu cache tree vào file JSON để mở nhanh (tùy chọn)

Yêu cầu:
 - Python 3.8+
 - pip install requests keyring pygments ttkbootstrap (tùy chọn)

Build .exe với PyInstaller (gợi ý):
 pip install pyinstaller
 pyinstaller --onefile --noconsole --hidden-import=keyring gitlab_gui_pro_full.py

Ghi chú: Code có comment tiếng Việt để dễ hiểu cho học sinh.
"""

import os
import sys
import json
import base64
import threading
import queue
import time
import traceback
import zipfile
from functools import partial
from urllib.parse import quote_plus

# Thử import các thư viện tùy chọn
try:
    import requests
    REQUESTS_AVAILABLE = True
except Exception:
    requests = None
    REQUESTS_AVAILABLE = False

try:
    import keyring
    KEYRING_AVAILABLE = True
except Exception:
    keyring = None
    KEYRING_AVAILABLE = False

try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename, guess_lexer
    from pygments.token import Token
    PYGMENTS_AVAILABLE = True
except Exception:
    PYGMENTS_AVAILABLE = False

# ttkbootstrap hỗ trợ theme dark; nếu không có, ta dùng ttk.Style thủ công
try:
    import ttkbootstrap as tb
    TTB_AVAILABLE = True
except Exception:
    tb = None
    TTB_AVAILABLE = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ---------------------------
# Cấu hình và hằng số
# ---------------------------
BASE_URL = os.environ.get('GITLAB_BASE_URL', 'https://git.rikkei.edu.vn/api/v4')
CACHE_FILE = os.path.join(os.path.expanduser('~'), '.gitlab_gui_cache.json')
TOKEN_FILE_FALLBACK = os.path.join(os.path.expanduser('~'), '.gitlab_gui_token')
KEYRING_SERVICE = 'gitlab_gui_pro_full'
KEYRING_USER = 'token'

# ---------------------------
# Hàm lưu / load token
# ---------------------------

def save_token(token: str):
    """Lưu token an toàn: ưu tiên keyring, fallback base64 vào file"""
    try:
        if KEYRING_AVAILABLE:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
            return True
        else:
            with open(TOKEN_FILE_FALLBACK, 'w', encoding='utf-8') as f:
                f.write(base64.b64encode(token.encode('utf-8')).decode('ascii'))
            try:
                os.chmod(TOKEN_FILE_FALLBACK, 0o600)
            except Exception:
                pass
            return True
    except Exception as e:
        print('Lỗi khi lưu token:', e)
        return False


def load_token():
    try:
        if KEYRING_AVAILABLE:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if os.path.exists(TOKEN_FILE_FALLBACK):
            with open(TOKEN_FILE_FALLBACK, 'r', encoding='utf-8') as f:
                b = f.read().strip()
                if b:
                    return base64.b64decode(b.encode('ascii')).decode('utf-8')
        return None
    except Exception as e:
        print('Lỗi khi load token:', e)
        return None


def delete_saved_token():
    try:
        if KEYRING_AVAILABLE:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        if os.path.exists(TOKEN_FILE_FALLBACK):
            try:
                os.remove(TOKEN_FILE_FALLBACK)
            except Exception:
                pass
        return True
    except Exception as e:
        print('Lỗi khi xóa token:', e)
        return False

# ---------------------------
# GitLabClient: wrapper API
# ---------------------------
class GitLabClient:
    def __init__(self, token: str = None, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.headers = {}
        if token:
            self.set_token(token)

    def set_token(self, token: str):
        self.token = token.strip()
        self.headers = {
            'PRIVATE-TOKEN': self.token,
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }

    def test(self):
        r = requests.get(f"{self.base_url}/user", headers=self.headers, timeout=10)
        if r.status_code == 200:
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def _paged_get(self, path, params=None):
        page = 1
        per_page = 100
        res = []
        while True:
            p = dict(params or {})
            p.update({'page': page, 'per_page': per_page})
            r = requests.get(f"{self.base_url}{path}", headers=self.headers, params=p, timeout=20)
            if r.status_code != 200:
                raise requests.HTTPError(f"{r.status_code}: {r.text}")
            data = r.json()
            if not data:
                break
            res.extend(data)
            if len(data) < per_page:
                break
            page += 1
        return res

    def list_groups(self):
        return self._paged_get('/groups')

    def create_group(self, name, parent_id=None):
        payload = {'name': name.strip(), 'path': slugify(name)}
        if parent_id:
            payload['parent_id'] = parent_id
        r = requests.post(f"{self.base_url}/groups", headers=self.headers, json=payload, timeout=15)
        if r.status_code in (200,201):
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def create_project(self, name, namespace_id=None):
        payload = {'name': name.strip()}
        if namespace_id:
            payload['namespace_id'] = namespace_id
        r = requests.post(f"{self.base_url}/projects", headers=self.headers, json=payload, timeout=20)
        if r.status_code in (200,201):
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def list_projects_in_group(self, group_id):
        r = requests.get(f"{self.base_url}/groups/{group_id}/projects", headers=self.headers, params={'per_page':100}, timeout=20)
        if r.status_code == 200:
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def list_repository_tree(self, project_id, path='', ref='master'):
        params = {'per_page':100}
        if path:
            params['path'] = path
        if ref:
            params['ref'] = ref
        r = requests.get(f"{self.base_url}/projects/{project_id}/repository/tree", headers=self.headers, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def get_file_raw(self, project_id, file_path, ref='master'):
        # GET /projects/:id/repository/files/:file_path/raw?ref=master  (note: file_path must be URL encoded)
        encoded = quote_plus(file_path)
        r = requests.get(f"{self.base_url}/projects/{project_id}/repository/files/{encoded}/raw", headers=self.headers, params={'ref': ref}, timeout=30)
        if r.status_code == 200:
            return r.content
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def upload_file(self, project_id, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError('File không tồn tại')
        with open(file_path, 'rb') as f:
            files = {'file': f}
            r = requests.post(f"{self.base_url}/projects/{project_id}/uploads", headers=self.headers, files=files, timeout=60)
        if r.status_code in (200,201):
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

    def commit_files(self, project_id, branch, commit_message, actions):
        # actions: list of {action: 'create'|'update', file_path: ..., content: '...'}
        payload = {'branch': branch, 'commit_message': commit_message, 'actions': actions}
        r = requests.post(f"{self.base_url}/projects/{project_id}/repository/commits", headers=self.headers, json=payload, timeout=60)
        if r.status_code in (200,201):
            return r.json()
        raise requests.HTTPError(f"{r.status_code}: {r.text}")

# ---------------------------
# Tiện ích nhỏ
# ---------------------------

def slugify(name: str) -> str:
    import re
    s = name.strip().lower()
    s = re.sub(r'\s+', '-', s)
    s = re.sub(r'[^a-z0-9\-]', '', s)
    return s[:255]

# Helper: wrapper file-like để theo dõi progress khi upload
class ProgressFile:
    def __init__(self, path, callback):
        self.path = path
        self.f = open(path, 'rb')
        try:
            self.total = os.path.getsize(path)
        except Exception:
            self.total = 0
        self.read_bytes = 0
        self.callback = callback
    def read(self, size=-1):
        chunk = self.f.read(size)
        if chunk:
            self.read_bytes += len(chunk)
            try:
                self.callback(self.read_bytes, self.total)
            except Exception:
                pass
        return chunk
    def __len__(self):
        return self.total
    def close(self):
        try:
            self.f.close()
        except Exception:
            pass

# ---------------------------
# GUI chính
# ---------------------------
class GitLabGUIApp:
    def __init__(self, root):
        # Theme: nếu ttkbootstrap có, dùng theme đẹp
        self.root = root
        if TTB_AVAILABLE:
            self.style = tb.Style('litera')
            self.dark_mode = False
        else:
            self.style = ttk.Style()
            self.dark_mode = False
        root.title('GitLab Desktop Client - Học sinh (tkinter)')
        root.geometry('1200x800')

        # Client và trạng thái
        self.client = None
        self.user_info = None
        self.token = None
        self.cache = {}
        self._all_groups = []
        # Ghi nhớ parent/subgroup để mặc định khi mở popup
        self.last_parent_name = None
        self.last_subgroup_name = None

        # Queue cho thread-safe logging
        self.log_queue = queue.Queue()

        # Tạo layout: top frame (token/login), left tree, right notebook (viewer, log, actions)
        self._build_top()
        self._build_left_tree()
        self._build_right_panel()
        self._build_status_bar()

        # Nếu có token đã lưu, nạp vào ô
        saved = load_token()
        if saved:
            self.token_var.set(saved)
            self.save_token_var.set(True)

        # Kiểm tra requests
        if not REQUESTS_AVAILABLE:
            messagebox.showerror('Thiếu thư viện', "Thư viện 'requests' chưa cài. Chạy: python -m pip install requests")
            # disable network buttons
            self.btn_login.config(state='disabled')
            self.btn_create_popup.config(state='disabled')
            self.btn_refresh_tree.config(state='disabled')

        # Kiểm tra pygments
        if not PYGMENTS_AVAILABLE:
            self.log('Pygments không có: highlight sẽ dùng plain text')

        # Khởi động background thread để xử lý log queue
        self._start_log_worker()

        # Load cache nếu có
        self._load_cache()

    # ---------------------------
    # Build GUI
    # ---------------------------
    def _build_top(self):
        frame = ttk.Frame(self.root, padding=8)
        frame.pack(fill='x')

        ttk.Label(frame, text='Nhập Access Token:').pack(side='left')
        self.token_var = tk.StringVar()
        self.entry_token = ttk.Entry(frame, textvariable=self.token_var, width=60, show='*')
        self.entry_token.pack(side='left', padx=6)
        ToolTip(self.entry_token, 'Dán Personal Access Token từ GitLab (scope: api)')

        self.save_token_var = tk.BooleanVar(value=False)
        self.chk_save = ttk.Checkbutton(frame, text='Lưu Token', variable=self.save_token_var)
        self.chk_save.pack(side='left', padx=6)

        self.btn_login = ttk.Button(frame, text='Đăng nhập', command=self.on_login)
        self.btn_login.pack(side='left', padx=4)

        self.btn_logout = ttk.Button(frame, text='Đăng xuất', command=self.on_logout)
        self.btn_logout.pack(side='left', padx=4)

        self.btn_delete_token = ttk.Button(frame, text='Xóa Token Lưu', command=self.on_delete_token)
        self.btn_delete_token.pack(side='left', padx=4)

        # Search nhanh
        ttk.Label(frame, text='Tìm kiếm:').pack(side='left', padx=(20,4))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side='left')
        self.btn_search = ttk.Button(frame, text='Tìm', command=self.on_search)
        self.btn_search.pack(side='left', padx=4)
        self.btn_clear_search = ttk.Button(frame, text='Clear', command=self.on_clear_search)
        self.btn_clear_search.pack(side='left')

        # Menu chế độ
        self.btn_theme = ttk.Button(frame, text='Dark mode', command=self.toggle_dark)
        self.btn_theme.pack(side='right')

    def _build_left_tree(self):
        left_frame = ttk.Frame(self.root)
        left_frame.pack(side='left', fill='y', padx=8, pady=8)

        lbl = ttk.Label(left_frame, text='Cây Group/Subgroup/Project')
        lbl.pack(anchor='nw')

        self.tree = ttk.Treeview(left_frame)
        self.tree.pack(fill='y', expand=True)
        self.tree.bind('<<TreeviewOpen>>', self.on_tree_open)
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        ToolTip(self.tree, 'Mở node để lazy-load subnodes. Chọn project để xem/upload.')

        btns = ttk.Frame(left_frame)
        btns.pack(fill='x')

        self.btn_refresh_tree = ttk.Button(btns, text='Làm mới', command=self.populate_groups)
        self.btn_refresh_tree.pack(side='left', padx=4, pady=6)

        self.btn_create_popup = ttk.Button(btns, text='Tạo...', command=self.open_create_popup)
        self.btn_create_popup.pack(side='left', padx=4)

        self.btn_export_cache = ttk.Button(btns, text='Lưu cache', command=self.save_cache)
        self.btn_export_cache.pack(side='left', padx=4)

    def _build_right_panel(self):
        right = ttk.Frame(self.root)
        right.pack(fill='both', expand=True, padx=8, pady=8)

        nb = ttk.Notebook(right)
        nb.pack(fill='both', expand=True)

        # Viewer tab
        self.viewer_frame = ttk.Frame(nb)
        nb.add(self.viewer_frame, text='Viewer')

        self.viewer_text = scrolledtext.ScrolledText(self.viewer_frame, wrap='none', font=('Consolas', 11))
        self.viewer_text.pack(fill='both', expand=True)
        self.viewer_text.configure(state='disabled')

        # Action buttons under viewer
        act = ttk.Frame(self.viewer_frame)
        act.pack(fill='x')
        self.btn_upload_file = ttk.Button(act, text='Upload file', command=self.action_upload_file)
        self.btn_upload_file.pack(side='left', padx=4, pady=4)
        self.btn_upload_folder = ttk.Button(act, text='Upload folder', command=self.action_upload_folder)
        self.btn_upload_folder.pack(side='left', padx=4)
        self.btn_view_file_download = ttk.Button(act, text='Download file', command=self.action_download_file)
        self.btn_view_file_download.pack(side='left', padx=4)

        # Log tab
        self.log_frame = ttk.Frame(nb)
        nb.add(self.log_frame, text='Log')
        self.log_area = scrolledtext.ScrolledText(self.log_frame, wrap='none', height=10)
        self.log_area.pack(fill='both', expand=True)
        self.log_area.configure(state='disabled')

        log_btns = ttk.Frame(self.log_frame)
        log_btns.pack(fill='x')
        self.btn_save_log = ttk.Button(log_btns, text='Lưu log', command=self.save_log)
        self.btn_save_log.pack(side='left', padx=4, pady=6)

        # Info tab
        self.info_frame = ttk.Frame(nb)
        nb.add(self.info_frame, text='Info')
        self.info_label = ttk.Label(self.info_frame, text='Chưa có thông tin')
        self.info_label.pack(anchor='nw', padx=6, pady=6)

        # Disable action buttons trước khi chọn project
        self.set_action_buttons_state(False)

    def _build_status_bar(self):
        self.status_var = tk.StringVar(value='Sẵn sàng')
        status = ttk.Label(self.root, textvariable=self.status_var, relief='sunken', anchor='w')
        status.pack(fill='x', side='bottom')

    # ---------------------------
    # Logging helper
    # ---------------------------
    def log(self, text: str):
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] {text}"
        self.log_queue.put(line)

    def _start_log_worker(self):
        def worker():
            while True:
                try:
                    line = self.log_queue.get()
                    if line is None:
                        break
                    self.log_area.configure(state='normal')
                    self.log_area.insert('end', line + '\n')
                    self.log_area.see('end')
                    self.log_area.configure(state='disabled')
                except Exception:
                    pass
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def save_log(self):
        try:
            initial = os.path.join(os.path.expanduser('~'), 'gitlab_client_log.txt')
            fn = filedialog.asksaveasfilename(title='Lưu log', defaultextension='.txt', initialfile=initial, filetypes=[('Text files','*.txt')])
            if not fn:
                return
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(self.log_area.get('1.0', 'end'))
            messagebox.showinfo('Đã lưu', f'Log đã lưu vào {fn}')
        except Exception as e:
            messagebox.showerror('Lỗi', f'Không thể lưu log: {e}')

    # ---------------------------
    # Cache
    # ---------------------------
    def save_cache(self):
        try:
            data = {'groups': self._all_groups, 'last_parent': self.last_parent_name, 'last_subgroup': self.last_subgroup_name}
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f'Lưu cache vào {CACHE_FILE}')
            messagebox.showinfo('Đã lưu', 'Cache đã lưu')
        except Exception as e:
            self.log(f'Lỗi lưu cache: {e}')
            messagebox.showerror('Lỗi', f'Không thể lưu cache: {e}')

    def _load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._all_groups = data.get('groups', [])
                # load last-used parent/subgroup if có
                self.last_parent_name = data.get('last_parent')
                self.last_subgroup_name = data.get('last_subgroup')
                self.log('Đã nạp cache groups')
                # Build minimal tree from cache (root groups)
                self.tree.delete(*self.tree.get_children())
                roots = [g for g in self._all_groups if not g.get('parent_id')]
                for rg in sorted(roots, key=lambda x: x.get('name','').lower()):
                    iid = f"group_{rg.get('id')}"
                    self.tree.insert('', 'end', iid=iid, text=rg.get('name'), values=('group', rg.get('id')))
                    self.tree.insert(iid, 'end', iid=f"{iid}_dummy", text='(mở để tải...)')
            except Exception as e:
                self.log(f'Lỗi nạp cache: {e}')

    # ---------------------------
    # Actions: login/logout/delete token
    # ---------------------------
    def on_login(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showerror('Lỗi', 'Vui lòng nhập Access Token')
            return
        if not REQUESTS_AVAILABLE:
            messagebox.showerror('Lỗi', "Chưa cài 'requests'. Chạy: python -m pip install requests")
            return
        def task():
            try:
                self.set_status('Đăng nhập...')
                c = GitLabClient(token=token)
                user = c.test()
                self.client = c
                self.user_info = user
                self.token = token
                self.log(f"Đăng nhập thành công: {user.get('name')}")
                self.set_status(f"Đăng nhập: {user.get('name')}")
                if self.save_token_var.get():
                    ok = save_token(token)
                    if ok:
                        self.log('Token đã được lưu an toàn')
                # tự động tải groups
                self.populate_groups()
            except Exception as e:
                self.log(f'Lỗi đăng nhập: {e}')
                messagebox.showerror('Lỗi đăng nhập', str(e))
                self.set_status('Đăng nhập thất bại')
        threading.Thread(target=task, daemon=True).start()

    def on_logout(self):
        self.client = None
        self.user_info = None
        self.token = None
        self.set_status('Đã đăng xuất')
        self.log('Đã đăng xuất')

    def on_delete_token(self):
        if messagebox.askyesno('Xác nhận', 'Bạn có muốn xóa token đã lưu không?'):
            ok = delete_saved_token()
            if ok:
                messagebox.showinfo('Đã xóa', 'Token đã được xóa khỏi hệ thống')
                self.log('Token lưu đã bị xóa')
            else:
                messagebox.showerror('Lỗi', 'Không thể xóa token lưu')

    # ---------------------------
    # Tree operations: populate groups, lazy load
    # ---------------------------
    def populate_groups(self):
        if not self.client:
            messagebox.showerror('Lỗi', 'Bạn cần đăng nhập trước')
            return
        def task():
            try:
                self.set_status('Tải groups...')
                groups = self.client.list_groups()
                self._all_groups = groups
                self.log(f'Tải {len(groups)} groups')
                # rebuild tree
                self.tree.delete(*self.tree.get_children())
                roots = [g for g in groups if not g.get('parent_id')]
                for rg in sorted(roots, key=lambda x: x.get('name','').lower()):
                    iid = f"group_{rg.get('id')}"
                    self.tree.insert('', 'end', iid=iid, text=rg.get('name'), values=('group', rg.get('id')))
                    self.tree.insert(iid, 'end', iid=f"{iid}_dummy", text='(mở để tải...)')
                # cập nhật combobox trong popup khi tạo
                # (nếu popup đang mở thì sẽ refresh tự động)
                self.set_status('Hoàn thành tải groups')
            except Exception as e:
                self.log(f'Lỗi tải groups: {e}')
                messagebox.showerror('Lỗi', f'Không thể tải groups: {e}')
                self.set_status('Lỗi tải groups')
        threading.Thread(target=task, daemon=True).start()

    def on_tree_open(self, event):
        item = self.tree.focus()
        if not item:
            return
        v = self.tree.item(item, 'values')
        if not v:
            return
        typ = v[0]
        obj_id = v[1]
        # Nếu đã tải con (không còn dummy) thì skip
        children = self.tree.get_children(item)
        if children and not any(str(c).endswith('_dummy') for c in children):
            return
        # remove dummy
        for c in children:
            if str(c).endswith('_dummy'):
                self.tree.delete(c)
        if typ == 'group':
            self._load_subgroups_and_projects(item, int(obj_id))
        elif typ == 'subgroup':
            self._load_projects_for_subgroup(item, int(obj_id))
        elif typ == 'project':
            # add repo root placeholder
            proj_id = int(obj_id)
            self.tree.insert(item, 'end', iid=f'project_{proj_id}_repo', text='Repository', values=('repo', proj_id))
            self.tree.insert(f'project_{proj_id}_repo', 'end', iid=f'project_{proj_id}_repo_dummy', text='(mở để tải...)')

    def _load_subgroups_and_projects(self, item, group_id):
        # load subgroups from cached _all_groups
        try:
            subgs = [g for g in (self._all_groups or []) if g.get('parent_id') == group_id]
            for sg in sorted(subgs, key=lambda x: x.get('name','').lower()):
                iid = f"subgroup_{sg.get('id')}"
                self.tree.insert(item, 'end', iid=iid, text=sg.get('name'), values=('subgroup', sg.get('id')))
                self.tree.insert(iid, 'end', iid=f"{iid}_dummy", text='(mở để tải...)')
            # load projects in this group
            projects = self.client.list_projects_in_group(group_id)
            for p in sorted(projects, key=lambda x: x.get('name','').lower()):
                iid = f"project_{p.get('id')}"
                self.tree.insert(item, 'end', iid=iid, text=f"[P] {p.get('name')}", values=('project', p.get('id')))
            self.log(f'Loaded subgroups ({len(subgs)}) and projects ({len(projects)}) for group id={group_id}')
        except Exception as e:
            self.log(f'Lỗi load subgroups/projects: {e}')

    def _load_projects_for_subgroup(self, item, subgroup_id):
        try:
            projects = self.client.list_projects_in_group(subgroup_id)
            for p in sorted(projects, key=lambda x: x.get('name','').lower()):
                iid = f"project_{p.get('id')}"
                self.tree.insert(item, 'end', iid=iid, text=f"[P] {p.get('name')}", values=('project', p.get('id')))
            self.log(f'Loaded projects ({len(projects)}) for subgroup id={subgroup_id}')
        except Exception as e:
            self.log(f'Lỗi load projects cho subgroup: {e}')

    def on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            self.set_action_buttons_state(False)
            return
        item = sel[0]
        v = self.tree.item(item, 'values')
        if not v:
            self.set_action_buttons_state(False)
            return
        typ, obj_id = v[0], v[1]
        if typ == 'project':
            self.set_action_buttons_state(True)
            self.current_project_id = int(obj_id)
            self.set_status(f'Chọn project id={obj_id}')
        elif typ == 'repo':
            # open repo root
            self.set_action_buttons_state(False)
            self.set_status('Repository root')
        elif typ == 'blob':
            self.set_action_buttons_state(False)
            self.open_blob_in_viewer(obj_id)
        else:
            self.set_action_buttons_state(False)

    def set_action_buttons_state(self, enabled: bool):
        state = 'normal' if enabled else 'disabled'
        self.btn_upload_file.config(state=state)
        self.btn_upload_folder.config(state=state)
        self.btn_view_file_download.config(state=state)

    # ---------------------------
    # Viewer: open file content and highlight
    # ---------------------------
    def open_blob_in_viewer(self, path):
        # path is repository path
        try:
            sel = self.tree.selection()
            if not sel:
                return
            item = sel[0]
            # find project id from ancestors
            proj_id = None
            parent = self.tree.parent(item)
            while parent:
                v = self.tree.item(parent, 'values')
                if v and v[0] == 'project':
                    proj_id = int(v[1])
                    break
                parent = self.tree.parent(parent)
            if proj_id is None:
                return
            self.set_status('Tải file...')
            content = self.client.get_file_raw(proj_id, path)
            try:
                text = content.decode('utf-8')
            except Exception:
                self.viewer_text.configure(state='normal')
                self.viewer_text.delete('1.0', 'end')
                self.viewer_text.insert('end', '[Binary file hoặc không phải UTF-8]')
                self.viewer_text.configure(state='disabled')
                return
            self.viewer_text.configure(state='normal')
            self.viewer_text.delete('1.0', 'end')
            self.viewer_text.insert('end', text)
            self.viewer_text.configure(state='disabled')
            # syntax highlight if pygments available
            if PYGMENTS_AVAILABLE:
                try:
                    lexer = get_lexer_for_filename(path)
                except Exception:
                    try:
                        lexer = guess_lexer(text)
                    except Exception:
                        lexer = None
                if lexer:
                    self._apply_syntax_highlight(text, lexer)
            self.set_status('Hoàn thành tải file')
            self.log(f'Xem file: {path} trong project id={proj_id}')
        except Exception as e:
            self.log(f'Lỗi xem file: {e}')
            messagebox.showerror('Lỗi', f'Không thể tải file: {e}')

    def _apply_syntax_highlight(self, text, lexer):
        try:
            tokens = list(lex(text, lexer))
            self.viewer_text.configure(state='normal')
            self.viewer_text.delete('1.0', 'end')
            # Map token types to simple colors
            token_color = {
                'Token.Comment': '#888888',
                'Token.Keyword': '#0000FF',
                'Token.Name.Function': '#007F00',
                'Token.String': '#B22222',
                'Token.Number': '#FF00FF',
            }
            idx = '1.0'
            for ttype, value in tokens:
                # insert value
                self.viewer_text.insert('end', value)
                # apply tag
                tag = str(ttype)
                if not self.viewer_text.tag_cget(tag, 'foreground'):
                    color = token_color.get(tag.split()[0], None)
                    if color:
                        self.viewer_text.tag_config(tag, foreground=color)
                self.viewer_text.tag_add(tag, idx, 'end')
                idx = self.viewer_text.index('end')
            self.viewer_text.configure(state='disabled')
        except Exception as e:
            self.log(f'Lỗi highlight: {e}')

    # ---------------------------
    # Repo viewer lazy-load and download
    # ---------------------------
    def action_download_file(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showerror('Lỗi', 'Vui lòng chọn file (blob) để download')
            return
        item = sel[0]
        v = self.tree.item(item, 'values')
        if not v or v[0] != 'blob':
            messagebox.showerror('Lỗi', 'Vui lòng chọn file (blob) để download')
            return
        path = v[1]
        # find project id
        proj_id = None
        parent = self.tree.parent(item)
        while parent:
            vv = self.tree.item(parent, 'values')
            if vv and vv[0] == 'project':
                proj_id = int(vv[1])
                break
            parent = self.tree.parent(parent)
        if proj_id is None:
            return
        try:
            content = self.client.get_file_raw(proj_id, path)
            # save
            initial = os.path.basename(path)
            fn = filedialog.asksaveasfilename(title='Tải file về', initialfile=initial)
            if not fn:
                return
            with open(fn, 'wb') as f:
                f.write(content)
            messagebox.showinfo('Hoàn thành', f'File đã được lưu tại {fn}')
            self.log(f'Tải file {path} từ project {proj_id} về {fn}')
        except Exception as e:
            messagebox.showerror('Lỗi', f'Không thể tải file: {e}')
            self.log(f'Lỗi tải file: {e}')

    def _on_repo_tree_open(self, event, tvitem=None):
        # This method is used for separate repo viewer (not used now) - left implementation
        pass

    # ---------------------------
    # Upload file / folder
    # ---------------------------
    def action_upload_file(self):
        if not hasattr(self, 'current_project_id') or not self.current_project_id:
            messagebox.showerror('Lỗi', 'Vui lòng chọn Project trước')
            return
        if not REQUESTS_AVAILABLE:
            messagebox.showerror('Lỗi', "Chưa cài 'requests'. Chạy: python -m pip install requests")
            return
        fn = filedialog.askopenfilename(title='Chọn file để upload')
        if not fn:
            return
        def task():
            try:
                self.set_status('Uploading...')
                win, updater, close = self.show_progress_window('Uploading file...')
                def cb(read_bytes, total):
                    win.after(0, lambda: updater(read_bytes, total))
                pf = ProgressFile(fn, cb)
                files = {'file': (os.path.basename(fn), pf)}
                headers = self.client.headers.copy()
                headers.pop('Content-Type', None)
                r = requests.post(f"{self.client.base_url}/projects/{self.current_project_id}/uploads", headers=headers, files=files, timeout=120)
                pf.close()
                close()
                if r.status_code in (200,201):
                    res = r.json()
                    url = res.get('url') or str(res)
                    self.log(f'Upload file {fn} -> {url} (project {self.current_project_id})')
                    win.after(0, lambda: self.toast(f'Upload hoàn tất: {url}', timeout=3000))
                else:
                    raise requests.HTTPError(f"{r.status_code}: {r.text}")
            except Exception as e:
                self.log(f'Lỗi upload: {e}')
                messagebox.showerror('Lỗi upload', str(e))
            finally:
                self.set_status('Sẵn sàng')
        threading.Thread(target=task, daemon=True).start()

    def action_upload_folder(self):
        if not hasattr(self, 'current_project_id') or not self.current_project_id:
            messagebox.showerror('Lỗi', 'Vui lòng chọn Project trước')
            return
        if not REQUESTS_AVAILABLE:
            messagebox.showerror('Lỗi', "Chưa cài 'requests'. Chạy: python -m pip install requests")
            return
        folder = filedialog.askdirectory(title='Chọn folder để upload (sẽ zip và upload)')
        if not folder:
            return
        # zip folder
        try:
            base = os.path.basename(folder.rstrip('/\\'))
            zipname = os.path.join(os.path.expanduser('~'), f"{base}.zip")
            with zipfile.ZipFile(zipname, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(folder):
                    for f in files:
                        fp = os.path.join(root, f)
                        arcname = os.path.relpath(fp, start=folder)
                        zf.write(fp, arcname)
            # upload zip
            def task():
                try:
                    self.set_status('Uploading zip...')
                    win, updater, close = self.show_progress_window('Uploading zip...')
                    def cb(read_bytes, total):
                        win.after(0, lambda: updater(read_bytes, total))
                    pf = ProgressFile(zipname, cb)
                    files = {'file': (os.path.basename(zipname), pf)}
                    headers = self.client.headers.copy()
                    headers.pop('Content-Type', None)
                    r = requests.post(f"{self.client.base_url}/projects/{self.current_project_id}/uploads", headers=headers, files=files, timeout=180)
                    pf.close()
                    close()
                    if r.status_code in (200,201):
                        res = r.json()
                        url = res.get('url') or str(res)
                        self.log(f'Upload folder {folder} as {zipname} -> {url}')
                        win.after(0, lambda: self.toast(f'Upload hoàn tất: {url}', timeout=3000))
                    else:
                        raise requests.HTTPError(f"{r.status_code}: {r.text}")
                except Exception as e:
                    self.log(f'Lỗi upload folder: {e}')
                    messagebox.showerror('Lỗi', str(e))
                finally:
                    self.set_status('Sẵn sàng')
            threading.Thread(target=task, daemon=True).start()
        except Exception as e:
            messagebox.showerror('Lỗi', f'Không thể nén folder: {e}')

    # ---------------------------
    # Create group/subgroup/project via popup
    # ---------------------------
    def open_create_popup(self):
        win = tk.Toplevel(self.root)
        win.title('Tạo Group/Subgroup/Project')
        win.geometry('480x260')

        ttk.Label(win, text='Chọn loại:').pack(anchor='w', padx=8, pady=(8,0))
        typ = tk.StringVar(value='group')
        rbf = ttk.Frame(win)
        rbf.pack(anchor='w', padx=8)
        ttk.Radiobutton(rbf, text='Group', variable=typ, value='group').pack(side='left')
        ttk.Radiobutton(rbf, text='Subgroup', variable=typ, value='subgroup').pack(side='left')
        ttk.Radiobutton(rbf, text='Project', variable=typ, value='project').pack(side='left')

        ttk.Label(win, text='Tên (name):').pack(anchor='w', padx=8, pady=(8,0))
        name_var = tk.StringVar()
        name_entry = ttk.Entry(win, textvariable=name_var, width=50)
        name_entry.pack(padx=8)
        name_entry.focus_set()

        ttk.Label(win, text='Parent Group (chỉ cho Subgroup/Project):').pack(anchor='w', padx=8, pady=(8,0))
        parent_combo = ttk.Combobox(win, values=[g.get('name') for g in self._all_groups if not g.get('parent_id')], state='readonly')
        parent_combo.pack(padx=8, fill='x')
        # nếu đã có last-used parent, chọn làm mặc định
        if self.last_parent_name:
            try:
                parent_combo.set(self.last_parent_name)
            except Exception:
                pass

        ttk.Label(win, text='(Nếu tạo Project trong Subgroup, chọn Subgroup sau khi chọn Group)').pack(anchor='w', padx=8, pady=(6,0))
        subgroup_combo = ttk.Combobox(win, values=[], state='readonly')
        subgroup_combo.pack(padx=8, fill='x')

        def on_parent_select(_event=None):
            sel = parent_combo.get()
            # lưu last-used parent
            self.last_parent_name = sel if sel else None
            if not sel:
                subgroup_combo['values'] = []
                return
            gid = None
            for g in self._all_groups:
                if g.get('name') == sel and not g.get('parent_id'):
                    gid = g.get('id')
                    break
            if gid is None:
                subgroup_combo['values'] = []
                return
            subs = [s.get('name') for s in self._all_groups if s.get('parent_id') == gid]
            subgroup_combo['values'] = subs
            # nếu có last_subgroup phù hợp thì đặt mặc định
            if self.last_subgroup_name and self.last_subgroup_name in subs:
                try:
                    subgroup_combo.set(self.last_subgroup_name)
                except Exception:
                    pass

        parent_combo.bind('<<ComboboxSelected>>', on_parent_select)
        # nếu đã có parent mặc định, populate subgroup
        if self.last_parent_name:
            on_parent_select()

        def clear_fields():
            name_var.set('')
            subgroup_combo.set('')
            parent_combo.set('')
            name_entry.focus_set()

        def do_create():
            name = name_var.get().strip()
            choice = typ.get()
            parent = parent_combo.get().strip()
            sub = subgroup_combo.get().strip()
            if not name:
                messagebox.showerror('Lỗi', 'Vui lòng nhập tên')
                return
            def task():
                try:
                    self.set_status('Đang tạo...')
                    if choice == 'group':
                        res = self.client.create_group(name)
                        self.last_parent_name = res.get('name')
                        self.last_subgroup_name = None
                        self.log(f'Tạo group: {name} (id={res.get("id")})')
                        win.after(0, lambda: self.toast(f"Đã tạo group {res.get('name')}", timeout=2000))
                        # thêm vào tree
                        iid = f"group_{res.get('id')}"
                        self.tree.insert('', 'end', iid=iid, text=res.get('name'), values=('group', res.get('id')))
                        self.tree.insert(iid, 'end', iid=f"{iid}_dummy", text='(mở để tải...)')
                        # cập nhật cache
                        self._all_groups = self.client.list_groups()
                        # Hành động sau khi tạo: giữ popup hay đóng tùy chọn
                        if keep_open_var.get():
                            # Clear fields and focus for next create
                            win.after(0, clear_fields)
                            try:
                                new_groups = self.client.list_groups()
                                self._all_groups = new_groups
                                win.after(0, lambda: parent_combo.configure(values=[g.get('name') for g in new_groups if not g.get('parent_id')]))
                                win.after(0, lambda: self.save_cache())
                            except Exception:
                                pass
                        else:
                            win.after(0, win.destroy)
                    elif choice == 'subgroup':
                        if not parent:
                            messagebox.showerror('Lỗi', 'Chọn Group cha')
                            return
                        g = self.find_group_by_name(parent)
                        if not g:
                            messagebox.showerror('Lỗi', 'Không tìm thấy Group cha')
                            return
                        res = self.client.create_group(name, parent_id=g.get('id'))
                        # lưu last-used parent/subgroup
                        self.last_parent_name = parent
                        self.last_subgroup_name = res.get('name')
                        self.log(f'Tạo subgroup: {name} (id={res.get("id")}) under group {parent}')
                        win.after(0, lambda: self.toast(f"Đã tạo subgroup {res.get('name')}", timeout=2000))
                        # cập nhật tree
                        parent_iid = f"group_{g.get('id')}"
                        if self.tree.exists(parent_iid):
                            self.tree.insert(parent_iid, 'end', iid=f"subgroup_{res.get('id')}", text=res.get('name'), values=('subgroup', res.get('id')))
                            self.tree.insert(f"subgroup_{res.get('id')}", 'end', iid=f"subgroup_{res.get('id')}_dummy", text='(mở để tải...)')
                        self._all_groups = self.client.list_groups()
                        # Hành động sau khi tạo
                        if keep_open_var.get():
                            win.after(0, clear_fields)
                            try:
                                new_groups = self.client.list_groups()
                                self._all_groups = new_groups
                                win.after(0, lambda: parent_combo.configure(values=[g.get('name') for g in new_groups if not g.get('parent_id')]))
                                win.after(0, lambda: self.save_cache())
                            except Exception:
                                pass
                        else:
                            win.after(0, win.destroy)
                    elif choice == 'project':
                        # quyết định namespace: ưu tiên chọn Subgroup, sau đó Group
                        namespace_id = None
                        if sub:
                            # find subgroup by name under parent
                            # find parent id
                            pg = self.find_group_by_name(parent) if parent else None
                            subs = [s for s in (self._all_groups or []) if s.get('name') == sub and s.get('parent_id') == (pg.get('id') if pg else None)]
                            if subs:
                                namespace_id = subs[0].get('id')
                        if namespace_id is None and parent:
                            gfound = self.find_group_by_name(parent)
                            if gfound:
                                namespace_id = gfound.get('id')
                        if namespace_id is None:
                            messagebox.showerror('Lỗi', 'Vui lòng chọn Group hoặc Subgroup làm namespace')
                            return
                        res = self.client.create_project(name, namespace_id=namespace_id)
                        # lưu last-used
                        if sub:
                            self.last_subgroup_name = sub
                        else:
                            self.last_parent_name = parent
                        self.log(f'Tạo project: {name} (id={res.get("id")}) in namespace {namespace_id}')
                        win.after(0, lambda: self.toast(f"Đã tạo project {res.get('name')}", timeout=2000))
                        # cập nhật tree: tìm parent node
                        parent_iid_group = f"group_{namespace_id}"
                        parent_iid_sub = f"subgroup_{namespace_id}"
                        parent_iid = None
                        if self.tree.exists(parent_iid_group):
                            parent_iid = parent_iid_group
                        elif self.tree.exists(parent_iid_sub):
                            parent_iid = parent_iid_sub
                        if parent_iid:
                            new_iid = f"project_{res.get('id')}"
                            self.tree.insert(parent_iid, 'end', iid=new_iid, text=f"[P] {res.get('name')}", values=('project', res.get('id')))
                        # Lưu last-used và cache
                        win.after(0, lambda: self.save_cache())
                        # Hành động sau khi tạo project
                        if keep_open_var.get():
                            win.after(0, clear_fields)
                        else:
                            win.after(0, win.destroy)
                    else:
                        messagebox.showerror('Lỗi', 'Kiểu không hợp lệ')
                except Exception as e:
                    messagebox.showerror('Lỗi', f'Không thể tạo: {e}')
                    self.log(f'Lỗi create: {e}')
                finally:
                    self.set_status('Sẵn sàng')
            threading.Thread(target=task, daemon=True).start()
            # Không đóng ngay; đóng hay giữ tùy vào tùy chọn 'keep_open_var'
        # Checkbox: giữ cửa sổ sau khi tạo (mặc định: giữ để tạo liên tiếp)
        keep_open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(win, text='Giữ cửa sổ sau khi tạo', variable=keep_open_var).pack(anchor='w', padx=8, pady=(6,0))
        # Buttons: Create, Clear, Close
        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text='Tạo', command=do_create).pack(side='left', padx=6)
        ttk.Button(btn_frame, text='Clear', command=clear_fields).pack(side='left', padx=6)
        ttk.Button(btn_frame, text='Đóng', command=win.destroy).pack(side='left', padx=6)

    def find_group_by_name(self, name: str):
        for g in (self._all_groups or []):
            if g.get('name', '').strip().lower() == name.strip().lower():
                return g
        return None

    # ---------------------------
    # Search feature: mở node chứa kết quả
    # ---------------------------
    def on_search(self):
        q = self.search_var.get().strip().lower()
        if not q:
            messagebox.showinfo('Tìm kiếm', 'Nhập từ khóa tìm kiếm')
            return
        # traverse _all_groups and projects in tree
        def task():
            try:
                self.set_status('Tìm kiếm...')
                # tìm group hoặc project
                matches = []
                for g in (self._all_groups or []):
                    if q in g.get('name','').lower():
                        matches.append(('group', g))
                # tìm projects by calling API for each group (could be heavy) - ta dùng tree loaded nodes first
                for gid in [n for n in self.tree.get_children('')]:
                    # expand and check children
                    for child in self.tree.get_children(gid):
                        text = self.tree.item(child, 'text')
                        if q in str(text).lower():
                            v = self.tree.item(child, 'values')
                            if v:
                                matches.append((v[0], {'id': int(v[1]), 'name': text}))
                if not matches:
                    messagebox.showinfo('Tìm kiếm', 'Không tìm thấy')
                    self.set_status('Không tìm thấy')
                    return
                # chọn kết quả đầu tiên: mở cây tương ứng
                typ, obj = matches[0]
                if typ == 'group':
                    iid = f"group_{obj.get('id')}"
                    if not self.tree.exists(iid):
                        # refresh groups
                        self.populate_groups()
                        time.sleep(1)
                    self.tree.see(iid)
                    self.tree.selection_set(iid)
                else:
                    # project or subgroup
                    iid = f"{typ}_{obj.get('id')}"
                    if not self.tree.exists(iid):
                        # try reloading group's children
                        self.populate_groups()
                        time.sleep(1)
                    self.tree.see(iid)
                    self.tree.selection_set(iid)
                self.set_status('Hoàn thành tìm kiếm')
            except Exception as e:
                self.log(f'Lỗi tìm kiếm: {e}')
                messagebox.showerror('Lỗi', f'Error: {e}')
            finally:
                self.set_status('Sẵn sàng')
        threading.Thread(target=task, daemon=True).start()

    def on_clear_search(self):
        self.search_var.set('')
        self.set_status('Sẵn sàng')

    # ---------------------------
    # UI helpers
    # ---------------------------
    def set_status(self, text):
        self.status_var.set(text)

    def toast(self, text: str, timeout: int = 2500):
        """Hiển thị thông báo non-modal, tự đóng sau timeout (ms)"""
        try:
            tw = tk.Toplevel(self.root)
            tw.wm_overrideredirect(True)
            tw.attributes("-topmost", True)
            lbl = ttk.Label(tw, text=text, relief='solid', padding=6)
            lbl.pack()
            # đặt ở góc trên trái của cửa sổ chính (offset nhỏ)
            x = self.root.winfo_rootx() + 50
            y = self.root.winfo_rooty() + 50
            tw.geometry(f'+{x}+{y}')
            self.root.after(timeout, lambda: tw.destroy())
        except Exception:
            pass

    def show_progress_window(self, title: str = 'Progress'):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry('420x100')
        ttk.Label(win, text=title).pack(pady=(8,0))
        pbar = ttk.Progressbar(win, orient='horizontal', mode='determinate', length=380)
        pbar.pack(pady=8)
        lbl = ttk.Label(win, text='0/0')
        lbl.pack()
        def updater(current, total):
            try:
                pbar.config(maximum=max(1, total), value=current)
                lbl.config(text=f'{current}/{total} bytes')
                win.update_idletasks()
            except Exception:
                pass
        def close():
            try:
                win.destroy()
            except Exception:
                pass
        return win, updater, close

    def toggle_dark(self):
        # nếu ttkbootstrap có thể toggle
        if TTB_AVAILABLE:
            self.dark_mode = not self.dark_mode
            theme = 'darkly' if self.dark_mode else 'litera'
            self.style.theme_use(theme)
            self.btn_theme.config(text='Light mode' if self.dark_mode else 'Dark mode')
            return
        # fallback: thay đổi background màu tối
        if not self.dark_mode:
            self.root.configure(bg='#2e2e2e')
            try:
                self.viewer_text.configure(background='#1e1e1e', foreground='#dcdcdc')
                self.log_area.configure(background='#1e1e1e', foreground='#dcdcdc')
            except Exception:
                pass
            self.dark_mode = True
            self.btn_theme.config(text='Light mode')
        else:
            self.root.configure(bg='SystemButtonFace')
            try:
                self.viewer_text.configure(background='white', foreground='black')
                self.log_area.configure(background='white', foreground='black')
            except Exception:
                pass
            self.dark_mode = False
            self.btn_theme.config(text='Dark mode')

# ---------------------------
# ToolTip class đơn giản
# ---------------------------
class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind('<Enter>', self.show)
        widget.bind('<Leave>', self.hide)

    def show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        lbl = tk.Label(tw, text=self.text, background='#ffffe0', relief='solid', borderwidth=1)
        lbl.pack()

    def hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# ---------------------------
# Chạy chương trình
# ---------------------------

def main():
    root = tk.Tk()
    app = GitLabGUIApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()

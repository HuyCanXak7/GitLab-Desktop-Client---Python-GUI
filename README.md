# GitLab Desktop Client - Pro Full GUI

**Mô tả:**  
Ứng dụng desktop GUI bằng Python (Tkinter) giúp tương tác GitLab dễ dàng. Hỗ trợ quản lý **Group / Subgroup / Project**, upload file/folder, xem code với **syntax highlight**, lazy-load treeview, dark mode, lưu log, cache nhanh, threading và progress bar.

**Tính năng chính:**
- Quản lý Access Token (keyring hoặc file base64 fallback)
- Treeview phân cấp: Group → Subgroup → Project → Repository → File (lazy-load)
- Tạo Group / Subgroup / Project trực tiếp từ GUI
- Upload file / folder (nén zip) + commit text files
- Xem file text/code với syntax highlight (pygments)
- Lưu log, export log
- Tìm kiếm nhanh và mở node chứa kết quả
- Dark mode toggle
- Threading cho các thao tác mạng và upload, progress bar cho nhiều file
- Lưu cache treeview vào JSON để mở nhanh

**Yêu cầu:**
- Python 3.8+
- Thư viện: `requests`, `keyring`, `pygments`, `ttkbootstrap` (tuỳ chọn)
- Chạy Windows / Linux / MacOS với GUI Tkinter

**Cài đặt:**

```bash
# clone repo
git clone https://github.com/HuyCanXak7/gitlab-desktop-client.git
cd gitlab-desktop-client

# cài dependencies
pip install requests keyring pygments ttkbootstrap
Chạy ứng dụng:

bash
Sao chép mã
python gitlab_gui_pro_full.py
Build file .exe (Windows) với PyInstaller:

bash
Sao chép mã
pip install pyinstaller
pyinstaller --onefile --noconsole --hidden-import=keyring gitlab_gui_pro_full.py
Hướng dẫn sử dụng:

Nhập Personal Access Token từ GitLab (scope: api)

Click “Đăng nhập” → tự động load groups & projects

Mở treeview để lazy-load subgroup / project / repository / file

Click project để upload file/folder hoặc xem code

Lưu cache, log, và sử dụng dark mode theo nhu cầu

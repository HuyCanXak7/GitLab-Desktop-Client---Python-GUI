# GitLab Desktop Client (Python GUI)

![GitLab Logo](https://about.gitlab.com/images/press/logo/png/gitlab-logo-gray-stacked-rgb.png)

Ứng dụng GUI **Python** giúp quản lý GitLab trực tiếp từ desktop. Dễ sử dụng, hỗ trợ tạo Group/Subgroup/Project, upload file/folder, xem file với highlight code, dark mode, lazy-load treeview và lưu cache.

---

## Tính năng chính

- Quản lý **Access Token** an toàn (Keyring hoặc file mã hóa Base64)
- **Treeview phân cấp**: Group → Subgroup → Project → Repository → File (lazy-load)
- **Tạo Group, Subgroup, Project** trực tiếp qua popup
- **Upload file/folder** (folder được nén zip trước khi upload)
- Xem file **text/code** với syntax highlight (dùng Pygments nếu có)
- **Download file** từ repository
- Lưu log, export log
- **Tìm kiếm nhanh** node trong tree
- **Dark mode toggle** (nếu có ttkbootstrap, fallback style bình thường)
- Threading cho thao tác mạng/upload, kèm **progress bar**
- **Lưu cache tree** vào file JSON để mở nhanh

---

## Yêu cầu

- Python 3.8+
- Các thư viện Python:

```bash
pip install requests keyring pygments ttkbootstrap

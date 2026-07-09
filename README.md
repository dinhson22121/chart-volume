# Chart-Volume

Ứng dụng desktop crawl dữ liệu cổ phiếu **VN30 + watchlist**, phân tích theo phương pháp **Wyckoff** (định lượng) và sinh nhận định/lời khuyên bằng **Claude**.

Kiến trúc **Hybrid**: bộ não là service **Python/FastAPI** (crawl vnstock → SQLite → Wyckoff rule engine → Claude narrative), vỏ là **Electron + React** khởi động backend như child process và giao tiếp qua loopback + token per-launch.

```
Electron main (Node)  ──spawn──▶  FastAPI (Python, 127.0.0.1:8787)
   mint token, mở cửa sổ            ├─ vnstock crawler (VCI)
   React UI ──REST(Bearer)──────────┤─ SQLite (candles + analyses)
   chart + watchlist + AI panel     ├─ Wyckoff rule engine
                                     └─ Claude narrative
```

## Yêu cầu
- **Python ≥ 3.10** (dùng 3.12; vnstock 3.x không chạy trên 3.9)
- **Node ≥ 18**

## Backend

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # điền ANTHROPIC_API_KEY nếu muốn nhận định AI
.venv/bin/pytest            # 39 test
# chạy standalone (tùy chọn):
LOCAL_API_TOKEN=dev .venv/bin/uvicorn app.main:app --port 8787
```

Khung thời gian:
- `daily` — nến ngày (nền phân tích chính, lịch sử dài).
- `half_session` — nến nửa phiên (sáng 09:00–11:30 / chiều 13:00–15:00), gom từ nến 1H. Chiều sâu intraday của vnstock hạn chế nên chỉ backfill được ít phiên gần đây.

## Desktop

```bash
cd desktop
npm install
npm run dev        # chạy Vite + Electron (Electron tự spawn backend từ ../backend/.venv)
```

Lệnh khác: `npm run typecheck`, `npm run build` (đóng gói renderer), `npm run dev:renderer` (chạy UI trong trình duyệt, cần `desktop/.env`).

## Cách dùng
1. Mở app → bấm **Tải VN30** để nạp rổ VN30.
2. Chọn một mã → bấm **Cập nhật** để crawl + phân tích.
3. Xem biểu đồ nến + volume, marker sự kiện Wyckoff, đường hỗ trợ/kháng cự và panel nhận định.
4. Thêm mã ngoài VN30 bằng ô nhập ở sidebar.

Scheduler tự crawl + phân tích VN30 + watchlist sau phiên sáng (~11:35), phiên chiều (~15:05) và sau close (~15:15).

## Đóng gói app độc lập (.app / .dmg)

Backend Python được đóng bằng **PyInstaller** (onedir) rồi nhúng vào app Electron qua **electron-builder** — user cài xong chạy được ngay, không cần Python/Node.

```bash
# 1) Bundle backend (ra backend/dist/chart-volume-backend/)
cd backend
.venv/bin/pip install pyinstaller
cp ~/.matplotlib/fontlist-*.json mpl_cache/    # seed font cache -> khởi động nhanh
.venv/bin/pyinstaller run_server.py --name chart-volume-backend --onedir --noconfirm --clean \
  --collect-all vnstock --collect-submodules uvicorn --collect-submodules app \
  --collect-submodules apscheduler --collect-submodules anthropic \
  --add-data "mpl_cache:mpl_cache" \
  --exclude-module tkinter --exclude-module PyQt5 --exclude-module PyQt6

# 2) Đóng gói Electron (ra desktop/release/)
cd ../desktop
npm run dist            # -> release/mac/Chart-Volume.app + release/Chart-Volume-0.1.0.dmg
```

Chi tiết kỹ thuật:
- `run_server.py` ép `MPLCONFIGDIR` sang thư mục ghi được cạnh DB và pre-seed font cache (vnstock kéo matplotlib) → cold start ~8–13s thay vì ~90s.
- Prod: `electron/main.cjs` spawn binary `Resources/backend/chart-volume-backend`; dev: spawn uvicorn từ venv.
- App **chưa ký (unsigned)** → lần đầu mở phải chuột phải → Open (hoặc `xattr -dr com.apple.quarantine Chart-Volume.app`).
- Muốn có nhận định AI trong bản đóng gói: đặt `ANTHROPIC_API_KEY` trong môi trường trước khi mở app (Electron truyền env xuống backend). *(Settings UI cho key là việc về sau.)*

## Lưu ý
- **vnstock là API không chính thức** → mọi call có retry + fail mềm; danh sách VN30 fallback về seed tĩnh khi endpoint lỗi (cập nhật thủ công theo quý).
- Phân tích Wyckoff là **heuristic**, luôn kèm disclaimer — **không phải khuyến nghị đầu tư**.
- Chi phí Claude giới hạn bằng cache theo `as_of` (chỉ gọi lại khi có nến mới).

## Cấu trúc
- `backend/app/{crawler,wyckoff,ai,services,api}` — crawl, rule engine, narrative, ingest/analysis, REST.
- `desktop/electron/` — main + preload (spawn Python, token bridge).
- `desktop/src/components/{watchlist,chart,analysis}` — UI.

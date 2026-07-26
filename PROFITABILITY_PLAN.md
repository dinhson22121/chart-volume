# Kế hoạch: Làm cho tín hiệu giao dịch đáng tin & khả thi khi làm theo

Tài liệu này ghi lại kế hoạch và phát hiện từ buổi rà soát "chạy có lợi nhuận" (2026-07-26). Khác với `DEVELOPMENT_ROADMAP.md` (tính năng mới), tài liệu này tập trung vào **tính đúng đắn của số liệu win-rate/expectancy hiện có** và các ràng buộc thực tế của thị trường VN + crypto spot.

**Ràng buộc đã chốt với user:** ứng dụng chỉ mô phỏng **mua/bán spot** (buy low, sell high) — không có long/short, không margin, không short-sell. Điều này áp dụng cho **cả cổ phiếu VN lẫn crypto**, không riêng gì cổ phiếu.

---

## Phase 0 — Điều tra look-ahead/repaint bias (ĐÃ XONG, không cần sửa)

**Giả thuyết ban đầu:** `app/smc/indicators.py::_is_swing_high/_is_swing_low` xác nhận một swing point bằng cửa sổ nhìn cả về tương lai (`right = highs.iloc[i+1:i+1+lookback]`). Nghi ngờ: `app/smc/events.py::_detect_structure` có thể dùng swing đã xác nhận để phát tín hiệu BOS/CHoCH tại một bar SỚM HƠN thời điểm swing đó thực sự có thể biết được trong thực tế (real-time).

**Phân tích chứng minh (bằng tay):** Gọi bar xác nhận swing là `j`, `lookback = L`. Để swing tại `j` được xác nhận `True`, MỌI bar trong cửa sổ phải `high[j+1..j+L] < high[j]`. Điều kiện phá vỡ (breakout) tại bar `i` là `close[i] > active_high` (= `high[j]`). Nếu `i` nằm trong `(j, j+L]` — tức nằm trong chính cửa sổ xác nhận — thì `close[i] ≤ high[i]`, mà `high[i] < high[j]` (điều kiện xác nhận), nên `close[i] < high[j]` luôn đúng ⇒ **không thể vừa "phá vỡ" vừa "xác nhận swing"** ở cùng một bar trong cửa sổ đó. Do đó breakout hợp lệ chỉ có thể xảy ra ở `i ≥ j+L+1` — đúng bằng thời điểm mà swing đã chắc chắn được biết trong real-time (chỉ cần đã có đủ `L` bar sau `j`). **Kết luận: không có repaint bug** ở đây — cơ chế tự ràng buộc đúng theo cách hay.

**Việc vẫn làm:** thêm 1 test hồi quy (`backend/tests/test_no_lookahead.py`) chạy `analyze()` trên toàn bộ chuỗi và trên từng phần cắt ngắn dần (`candles[:i+1]`), so khớp toàn bộ event ở mọi bar đã qua — khoá lại tính chất "nhân quả" (causal) này cho cả 3 chiến lược (Wyckoff, SMC, SonicR), để một refactor sau này (đổi cửa sổ rolling, bỏ `.shift(1)`, đổi điều kiện xác nhận swing...) không âm thầm phá vỡ nó.

---

## Phase 1 — Chặn triệt để "short": chỉ tạo TradeScenario cho tín hiệu tăng giá

**Vấn đề:** `app/services/trade_scenario.py::_create_scenarios` hiện tạo TradeScenario cho CẢ tín hiệu bullish lẫn bearish (dòng `qualifying = [... e.type in bullish_events or e.type in bearish_events ...]`), coi bearish event như một lệnh "short" (entry = giá hiện tại, SL phía trên, TP phía dưới). Vì app chỉ mô phỏng spot, "short" này không bao giờ thực hiện được ngoài đời (không vay được cổ phiếu/coin để bán trước) ⇒ mọi P&L phía short trong `expectancy_r`/`total_pnl_amount` hiện tại là **ảo, làm méo con số kỳ vọng lợi nhuận tổng**.

**Việc làm:**
- `qualifying` chỉ giữ `e.type in bullish_events` (bỏ nhánh bearish) — cùng cách `_CONTINUATION_EVENT_TYPES` đã bị loại trừ, không phải "chọn rồi từ chối".
- Bỏ tham số `bearish_events` khỏi `_create_scenarios`/`sync_scenarios` (không còn được dùng ở đâu khác trong file) và cập nhật lời gọi ở `app/services/analysis.py`.
- `signal_outcomes.py` (thống kê win-rate theo hướng tín hiệu) **giữ nguyên** — bearish event vẫn có giá trị phân tích (biết tín hiệu giảm có đáng tin để THOÁT vị thế long không), chỉ là không sinh ra một "kịch bản giao dịch" short nữa.
- Cập nhật `test_trade_scenario.py`: các test hiện dựng scenario bearish (short) cần đổi kỳ vọng — không còn TradeScenario nào được tạo cho event bearish.

---

## Phase 2 — Chi phí giao dịch thực tế (phí + thuế) cho cổ phiếu VN

**Vấn đề:** `settings_service.py` mới chỉ có `slippage_pct_stock`/`slippage_pct_crypto`. Chưa có phí môi giới + thuế bán — với cổ phiếu VN, một vòng mua–bán mất thêm ~0.2–0.3% (phí 2 chiều ~0.1–0.15%/lượt + thuế bán 0.1%) so với chỉ trượt giá.

**Việc làm:**
- Thêm settings mới: `fee_pct_stock` (mặc định ví dụ `0.25`, gồm phí 2 chiều + thuế bán gộp lại — round-trip, không phải mỗi chiều) và `fee_pct_crypto` (mặc định thấp hơn, ví dụ `0.1`, do phí sàn crypto rẻ hơn).
- `get_risk_config()` trả thêm 2 khoá này.
- `get_scenario_stats()._r_multiple`: trừ thêm chi phí round-trip (fee_pct) bên cạnh slippage đã có, trước khi quy ra R-multiple.

---

## Phase 3 — Ràng buộc T+2.5 (settlement) cho cổ phiếu VN

**Vấn đề:** Cổ phiếu mua xong ~2.5 ngày làm việc sau mới về tài khoản để bán được (T+2.5). `_update_active_scenarios` hiện kiểm tra TP/SL ngay từ bar kế tiếp — với `timeframe=daily`, nếu TP/SL bị chạm ở bar 1–2 thì đây là kịch bản **không thể khớp lệnh thật ngoài đời** cho cổ phiếu (không áp dụng cho crypto — giao dịch 24/7, T+0).

**Việc làm:**
- Thêm hằng số `SETTLEMENT_BARS_STOCK = 3` (daily) — cho `half_session` cần quy đổi (~5 half-session bar ≈ T+2.5, 2 phiên/ngày).
- Trong `_update_active_scenarios`, với scenario thuộc `asset_class == stock`, bỏ qua kiểm tra TP/SL cho tới khi đã đủ số bar settlement tối thiểu kể từ `event_ts` (scenario vẫn "active", không bị đóng non).
- Crypto: không áp dụng, giữ nguyên hành vi hiện tại.

---

## Phát hiện thêm ngoài kế hoạch: API settings không lưu được risk config

Khi thêm `fee_pct_stock`/`fee_pct_crypto`, phát hiện `app/api/settings.py::SettingsIn` (Pydantic model cho `PUT /settings`) **chưa từng khai báo** `notional_capital`, `risk_pct_per_trade`, `slippage_pct_stock`, `slippage_pct_crypto`, `max_concurrent_scenarios`, `max_concurrent_scenarios_crypto` — dù `settings_service.py` và Settings UI (`SettingsModal.tsx`) đã hỗ trợ đầy đủ từ commit "risk management" trước đó. Pydantic mặc định bỏ qua field lạ ⇒ **toàn bộ mục "Quản trị rủi ro" trong Settings UI không lưu được khi bấm Save**, âm thầm không báo lỗi. Đã bổ sung 8 field (6 field cũ + 2 field phí mới) vào `SettingsIn` kèm validation, và thêm test `test_put_settings_accepts_risk_settings`.

## Việc CHƯA làm trong đợt này (để vòng sau)

- **Equity curve / mô phỏng vốn danh mục + max drawdown**: `expectancy_r` đã có, nhưng chưa nối các scenario thành một đường vốn tuần tự (risk_pct_per_trade, giới hạn vị thế đồng thời) để tính drawdown thực — đây là phần lõi của "Visual Backtester" (mục 3, `DEVELOPMENT_ROADMAP.md`).
- **Walk-forward validation** cho ngưỡng Wyckoff/SMC/SonicR do user tự chỉnh (tránh overfit ngưỡng trên chính dữ liệu đo win-rate).
- **Survivorship bias** của rổ VN30/Top100 (mã bị loại khỏi rổ biến mất khỏi mẫu).

---

## Trạng thái thực thi

- [x] Phase 0: test hồi quy causal cho 3 chiến lược (`tests/test_no_lookahead.py`) — PASS, xác nhận không có repaint
- [x] Phase 1: chặn short, chỉ tạo scenario bullish (spot-only, cả stock lẫn crypto)
- [x] Phase 2: settings phí giao dịch (`fee_pct_stock`/`fee_pct_crypto`) + áp vào `expectancy_r`/`total_pnl_amount`
- [x] Phase 3: gate T+2.5 (`SETTLEMENT_BARS_STOCK`) cho cổ phiếu trong `_update_active_scenarios`
- [x] Sửa lỗi phát sinh: `SettingsIn` API thiếu 8 field risk-management (không lưu được từ UI)
- [x] Cập nhật Settings UI (`SettingsModal.tsx`, `types.ts`, i18n vi/en) cho 2 field phí mới
- [x] Chạy lại toàn bộ `pytest` backend (tất cả xanh) + `tsc --noEmit` frontend (sạch)

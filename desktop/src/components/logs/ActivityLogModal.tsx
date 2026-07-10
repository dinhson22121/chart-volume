import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { ConfigChangeLogEntry, SystemAction, SystemActionLogEntry } from "../../types";
import "../stats/stats.css";

interface Props {
  onClose: () => void;
}

type Tab = "config" | "system";

const PAGE_SIZE = 20;

const ACTION_LABEL: Record<SystemAction, string> = {
  screener_scan: "Quét crypto",
  vn30_seed: "Seed VN30",
  half_session_morning: "Nửa phiên sáng",
  half_session_afternoon: "Nửa phiên chiều",
  daily_close: "Đóng phiên ngày",
  crypto_analysis_refresh: "Làm mới phân tích crypto",
};

const TRIGGER_LABEL: Record<SystemActionLogEntry["trigger"], string> = {
  manual: "Thủ công",
  scheduled: "Tự động",
};

const STATUS_LABEL: Record<SystemActionLogEntry["status"], string> = {
  running: "Đang chạy",
  success: "Thành công",
  error: "Lỗi",
  cancelled: "Đã hủy",
};

const STATUS_COLOR: Record<SystemActionLogEntry["status"], string> = {
  running: "var(--warn)",
  success: "var(--bull)",
  error: "var(--bear)",
  cancelled: "var(--text-faint)",
};

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("vi-VN", { dateStyle: "medium", timeStyle: "medium" });
}

export function ActivityLogModal({ onClose }: Props) {
  const [tab, setTab] = useState<Tab>("config");
  const [configItems, setConfigItems] = useState<ConfigChangeLogEntry[] | null>(null);
  const [systemItems, setSystemItems] = useState<SystemActionLogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPage(1);
  }, [tab]);

  useEffect(() => {
    setError(null);
    if (tab === "config") {
      api
        .getConfigLogs(page, PAGE_SIZE)
        .then((res) => {
          setConfigItems(res.items);
          setTotal(res.total);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Không tải được nhật ký"));
    } else {
      api
        .getSystemLogs(page, PAGE_SIZE)
        .then((res) => {
          setSystemItems(res.items);
          setTotal(res.total);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Không tải được nhật ký"));
    }
  }, [tab, page]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>Nhật ký</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <div className="wl-tabs" style={{ marginBottom: "var(--space-3)", maxWidth: 280 }}>
            <button className={tab === "config" ? "is-active" : ""} onClick={() => setTab("config")}>
              Cấu hình
            </button>
            <button className={tab === "system" ? "is-active" : ""} onClick={() => setTab("system")}>
              Hệ thống
            </button>
          </div>

          {error && <p className="settings-error">{error}</p>}

          {tab === "config" && (
            <>
              {!configItems && !error && <p className="faint">Đang tải…</p>}
              {configItems && configItems.length === 0 && (
                <p className="faint">Chưa có thay đổi cấu hình nào.</p>
              )}
              {configItems && configItems.length > 0 && (
                <div className="stats-table-wrap">
                  <table className="stats-table">
                    <thead>
                      <tr>
                        <th>Lúc</th>
                        <th>Trường</th>
                        <th>Giá trị cũ</th>
                        <th>Giá trị mới</th>
                      </tr>
                    </thead>
                    <tbody>
                      {configItems.map((e) => (
                        <tr key={e.id}>
                          <td className="faint">{formatDateTime(e.changed_at)}</td>
                          <td className="mono">{e.key}</td>
                          <td className="mono faint">{e.old_value || "—"}</td>
                          <td className="mono">{e.new_value || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {tab === "system" && (
            <>
              {!systemItems && !error && <p className="faint">Đang tải…</p>}
              {systemItems && systemItems.length === 0 && (
                <p className="faint">Chưa có hành động nào được ghi lại.</p>
              )}
              {systemItems && systemItems.length > 0 && (
                <div className="stats-table-wrap">
                  <table className="stats-table">
                    <thead>
                      <tr>
                        <th>Bắt đầu</th>
                        <th>Hành động</th>
                        <th>Kích hoạt</th>
                        <th>Trạng thái</th>
                        <th>Kết thúc</th>
                        <th>Chi tiết</th>
                      </tr>
                    </thead>
                    <tbody>
                      {systemItems.map((e) => (
                        <tr key={e.id}>
                          <td className="faint">{formatDateTime(e.started_at)}</td>
                          <td>{ACTION_LABEL[e.action] ?? e.action}</td>
                          <td className="faint">{TRIGGER_LABEL[e.trigger]}</td>
                          <td>
                            <span
                              style={{
                                display: "inline-block",
                                padding: "3px 10px",
                                borderRadius: 999,
                                fontSize: "var(--text-xs)",
                                fontWeight: 700,
                                color: "oklch(18% 0.02 250)",
                                backgroundColor: STATUS_COLOR[e.status],
                              }}
                            >
                              {STATUS_LABEL[e.status]}
                            </span>
                          </td>
                          <td className="faint">{e.finished_at ? formatDateTime(e.finished_at) : "—"}</td>
                          <td className="faint">{e.detail ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {total > PAGE_SIZE && (
            <div style={{ display: "flex", justifyContent: "center", gap: "var(--space-3)", marginTop: "var(--space-3)" }}>
              <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Trước
              </button>
              <span className="faint mono" style={{ alignSelf: "center" }}>
                {page}/{totalPages}
              </span>
              <button className="btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                Sau
              </button>
            </div>
          )}
        </div>

        <footer className="settings-modal__footer">
          <button className="btn" onClick={onClose}>
            Đóng
          </button>
        </footer>
      </div>
    </div>
  );
}

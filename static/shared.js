// File: static/shared.js
// Summary: Same-origin JSON requests and safe rendering/downloading of meeting artifacts.
export const $ = (selector) => document.querySelector(selector);
export const languages = {
  ja: "日本語",
  en: "English",
  zh: "中文",
  ko: "한국어",
  vi: "Tiếng Việt",
};
export function message(text, error = false) {
  const target = $("#message");
  target.textContent = text;
  target.className = `message notice${error ? " error" : ""}`;
}
export async function api(path, { method = "GET", body, token } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : `操作に失敗しました (${response.status})`,
    );
  }
  return response.json();
}
export async function download(path, filename, token) {
  const response = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok)
    throw new Error(
      "ファイルをダウンロードできません。処理状況を確認してください。",
    );
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
export function renderJobs(container, jobs, prefix, token, admin = false) {
  container.replaceChildren();
  const labels = {
    recorded: "録画保存済み・解析停止",
    pending: "処理待ち",
    waiting_key: "APIキー設定待ち",
    processing: "解析中",
    done: "完了",
    error: "エラー",
  };
  if (!jobs.length) {
    container.textContent =
      "録画や文字起こしから議事録を作成すると、ここに表示されます。";
    return;
  }
  for (const job of jobs) {
    const item = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${new Date(job.created * 1000).toLocaleString("ja-JP")} / ${labels[job.status] || job.status}`;
    item.append(summary);
    if (job.error) {
      const error = document.createElement("p");
      error.textContent = job.error;
      item.append(error);
    }
    if (job.notes) {
      const pre = document.createElement("pre");
      const notes = job.notes;
      pre.textContent = `${notes.title}\n\n${notes.summary}\n\n決定事項\n${notes.decisions.map((x) => "・" + x).join("\n")}\n\nタスク\n${notes.action_items.map((x) => `・${x.task}（担当: ${x.owner} / 期限: ${x.due}）\n 根拠: ${x.evidence}`).join("\n")}\n\n確認が必要な点\n${notes.uncertainties.join("\n")}`;
      item.append(pre);
    }
    const actions = document.createElement("div");
    actions.className = "actions";
    const exports = [
      ["video", "録画 MP4", "recording.mp4"],
      ["transcript", "文字起こし", "transcript.txt"],
      ["minutes", "議事録", "minutes.md"],
      ["tasks", "タスク CSV", "tasks.csv"],
    ];
    for (const [kind, label, filename] of exports) {
      if (
        (kind === "video" &&
          (!job.has_video || (!admin && !container.dataset.host))) ||
        (kind !== "video" && !job.transcript) ||
        (["minutes", "tasks"].includes(kind) && !job.notes)
      )
        continue;
      const button = document.createElement("button");
      button.textContent = label;
      button.onclick = () =>
        download(`${prefix}/${job.id}/download/${kind}`, filename, token).catch(
          (e) => message(e.message, true),
        );
      actions.append(button);
    }
    if (admin && job.status !== "processing") {
      const retry = document.createElement("button");
      retry.textContent = job.status === "recorded" ? "解析を開始" : "再実行";
      retry.onclick = async () => {
        try {
          await api(`/ai/api/admin/jobs/${job.id}/retry`, { method: "POST" });
          message("再実行を受け付けました。");
        } catch (e) {
          message(e.message, true);
        }
      };
      actions.append(retry);
    }
    item.append(actions);
    container.append(item);
  }
}

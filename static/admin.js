// File: static/admin.js
// Summary: Manage AI settings without exposing saved keys and revoke the administrator session on sign-out.
import { $, api, message, renderJobs } from "./shared.js";

// セッションの失効に成功した場合だけサインイン画面へ移動し、失敗時は再操作できるようにする。
$("#sign-out").onclick = async () => {
  const button = $("#sign-out");
  button.disabled = true;
  try {
    await api("/ai/api/admin/logout", { method: "POST" });
    window.location.replace("/ai/login?signed-out=1");
  } catch (error) {
    message(error.message, true);
    button.disabled = false;
  }
};

// 戻る操作で画面が復元された場合も、サーバーでセッションを再確認する。
window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});
const fields = {
  transcription_model: "transcription-model",
  recording_transcription_model: "recording-transcription-model",
  subtitle_model: "subtitle-model",
  translation_model: "translation-model",
  minutes_model: "minutes-model",
  max_speakers: "max-speakers",
  audio_minutes: "audio-minutes",
};
const modelFields = Object.entries(fields).filter(([key]) =>
  key.endsWith("_model"),
);
// 保存したモデルが一覧にない場合も値を維持し、未確認であることを明示する。
function options(select, values, current) {
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value.id;
    option.textContent = value.label;
    select.append(option);
  }
  if (current && !values.some((x) => x.id === current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current + "（保存済み・一覧では未確認）";
    select.prepend(option);
  }
  select.value = current || values[0]?.id || "";
}
// APIキーに紐づく候補を用途別に取得する。専用通訳モデルは取得件数も表示する。
async function loadModels() {
  const button = $("#reload-models");
  button.disabled = true;
  $("#model-status").textContent = "利用可能なモデルを取得しています…";
  try {
    const x = await api("/ai/api/admin/models");
    for (const [key, id] of modelFields) {
      const select = $("#" + id);
      options(select, x.models[key] || [], select.value);
    }
    $("#model-status").textContent =
      `APIキーのモデル候補を取得しました。音声通訳は${x.models.translation_model.length}件です。`;
  } catch (error) {
    $("#model-status").textContent =
      "候補を取得できません。キーを保存して再取得してください。";
    throw error;
  } finally {
    button.disabled = false;
  }
}
// サーバーは保存済みキーを返さないため、状態とモデル設定だけをフォームに反映する。
async function settings() {
  const x = await api("/ai/api/admin/settings");
  for (const [key, id] of Object.entries(fields)) {
    const input = $("#" + id);
    if (key.endsWith("_model")) options(input, [], x[key]);
    else input.value = x[key];
  }
  $("#auto-minutes").checked = x.auto_minutes;
  $("#key-status").textContent = x.configured
    ? "APIキー設定済み"
    : "APIキー未設定";
  $("#key-status").classList.toggle("ok", x.configured);
  if (x.configured) await loadModels();
}
$("#reload-models").onclick = () =>
  loadModels().catch((e) => message(e.message, true));
// 参加者用の会議URLと、記録にアクセスする権限付きURLを、それぞれの表示欄へ設定する。
function showLinks(x) {
  $("#links").hidden = false;
  $("#app-links").hidden = false;
  $("#host-link").href = x.host_url;
  $("#host-link").textContent = "主催者用の会議画面を開く";
  $("#guest-link").value = x.conference_url || x.guest_url;
  $("#guest-dashboard").href = x.guest_url;
  $("#app-ios").href = x.ios_url;
  $("#app-android").href = x.android_url;
  $("#open-meeting").href = x.host_url;
}
$("#settings-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  try {
    const body = { auto_minutes: $("#auto-minutes").checked };
    for (const [key, id] of Object.entries(fields))
      body[key] = ["max_speakers", "audio_minutes"].includes(key)
        ? Number($("#" + id).value)
        : $("#" + id).value.trim();
    if ($("#api-key").value.trim()) body.api_key = $("#api-key").value.trim();
    await api("/ai/api/admin/settings", { method: "PUT", body });
    $("#api-key").value = "";
    message("設定を保存しました。API接続を確認してください。");
    await settings();
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
  }
};
$("#check-key").onclick = async () => {
  const b = $("#check-key");
  b.disabled = true;
  try {
    const x = await api("/ai/api/admin/check-key", { method: "POST" });
    message(
      "API認証に成功しました。\n" +
        Object.entries(x.models)
          .map(
            ([model, ok]) =>
              `${model}: ${ok ? "一覧に存在" : "一覧では確認できません"}`,
          )
          .join("\n") +
        "\n" +
        x.notice,
    );
  } catch (e) {
    message(e.message, true);
  } finally {
    b.disabled = false;
  }
};
$("#delete-key").onclick = async () => {
  if (!confirm("保存済みAPIキーを削除し、進行中のAI通訳を停止しますか。"))
    return;
  try {
    await api("/ai/api/admin/key", { method: "DELETE" });
    await settings();
    message("APIキーを削除しました。");
  } catch (e) {
    message(e.message, true);
  }
};
$("#create-form").onsubmit = async (e) => {
  e.preventDefault();
  e.submitter.disabled = true;
  try {
    showLinks(
      await api("/ai/api/admin/rooms", {
        method: "POST",
        body: { title: $("#title").value },
      }),
    );
    await refresh();
    message("会議を作成しました。参加者リンクを共有してください。");
  } catch (error) {
    message(error.message, true);
  } finally {
    e.submitter.disabled = false;
  }
};
$("#copy-link").onclick = () =>
  navigator.clipboard
    .writeText($("#guest-link").value)
    .then(() => message("参加者リンクをコピーしました。"))
    .catch(() => message("リンク欄からコピーしてください。", true));
async function refresh() {
  const [rooms, jobs] = await Promise.all([
    api("/ai/api/admin/rooms"),
    api("/ai/api/admin/jobs"),
  ]);
  $("#rooms").replaceChildren();
  for (const room of rooms) {
    const row = document.createElement("tr");
    const expired = room.expires < Date.now() / 1000;
    for (const value of [
      room.title,
      new Date(room.created * 1000).toLocaleString("ja-JP"),
      `${Math.round(room.audio_seconds / 60)}分`,
      room.closed ? "終了" : expired ? "期限切れ" : "利用可能",
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    const cell = document.createElement("td");
    if (!room.closed && !expired) {
      const button = document.createElement("button");
      button.textContent = "リンクを発行";
      button.onclick = () =>
        api(`/ai/api/admin/rooms/${room.id}/links`, { method: "POST" })
          .then(showLinks)
          .catch((e) => message(e.message, true));
      cell.append(button);
    }
    row.append(cell);
    $("#rooms").append(row);
  }
  renderJobs($("#jobs"), jobs, "/ai/api/admin/jobs", null, true);
}
Promise.all([settings(), refresh()]).catch((e) => message(e.message, true));
setInterval(() => {
  if (!document.hidden) refresh().catch(() => {});
}, 15000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh().catch(() => {});
});

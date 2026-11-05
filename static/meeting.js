// File: static/meeting.js
// Summary: Use standard Jitsi media/translation controls on web and native apps, with protected recording/minutes access.
import { $, api, message, renderJobs } from "./shared.js";
const room = location.pathname.split("/").pop();
const invitation = new URLSearchParams(location.hash.slice(1));
const token =
  invitation.get("host") ||
  invitation.get("join") ||
  sessionStorage.getItem(`meeting-${room}`) ||
  "";
if (token) sessionStorage.setItem(`meeting-${room}`, token);
// The upstream SDK parses URL fragments; remove private capabilities before loading it.
history.replaceState(null, "", location.pathname);
let info,
  jitsi,
  recording = false,
  closed = false;
const roomApi = (path, options = {}) =>
  api(`/ai/api/rooms/${room}${path}`, { ...options, token });

// 認可済みの会議だけを定期取得する。ダウンロードには同じ招待権限を使用する。
async function refreshJobs() {
  if (!info) return;
  renderJobs(
    $("#jobs"),
    await roomApi("/jobs"),
    `/ai/api/rooms/${room}/jobs`,
    token,
  );
}

// マイクと音声再生はJitsiに任せ、公式アプリと共通の字幕・通訳トラックを使用する。
async function join(event) {
  event.preventDefault();
  const name = $("#name").value.trim();
  if (!name || !info || info.closed) return;
  sessionStorage.setItem("meeting-name", name);
  try {
    await new Promise((resolve, reject) => {
      if (window.JitsiMeetExternalAPI) {
        resolve();
        return;
      }
      const script = document.createElement("script");
      script.src = "/external_api.js";
      script.onload = resolve;
      script.onerror = reject;
      document.head.append(script);
    });
    jitsi = new JitsiMeetExternalAPI(location.hostname, {
      roomName: `ai-${room}`,
      parentNode: $("#conference"),
      width: "100%",
      height: "100%",
      lang: "ja",
      userInfo: { displayName: name },
      // Standard toolbar includes subtitles and audio translation, matching the official mobile clients.
      configOverwrite: {
        defaultLanguage: "ja",
        prejoinConfig: { enabled: false },
        disableDeepLinking: true,
        startWithVideoMuted: true,
      },
      interfaceConfigOverwrite: { MOBILE_APP_PROMO: false },
    });
    $("#join-form").closest(".empty-stage").remove();
    jitsi.addListener("videoConferenceJoined", () => {
      $("#record").disabled = info.role !== "host";
    });
    jitsi.addListener("participantRoleChanged", (value) => {
      if (value.role === "moderator" && info.role === "host")
        jitsi.executeCommand("subject", info.title);
    });
    jitsi.addListener("recordingStatusChanged", (value) => {
      recording = value.on;
      $("#record").disabled = false;
      $("#record").textContent = recording ? "録画を停止" : "録画を開始";
      $("#record-status").textContent = recording
        ? "サーバーに録画中"
        : "録画停止中 / 保存後に解析します";
      $("#record-status").classList.toggle("recording-on", recording);
      if (value.error) message("録画エラー: " + value.error, true);
    });
    jitsi.addListener("readyToClose", () => {
      closed = true;
      $("#record").disabled = true;
      $("#conference").replaceChildren();
      const p = document.createElement("p");
      p.className = "empty-stage";
      p.textContent = "会議から退出しました。右側で記録を確認できます。";
      $("#conference").append(p);
      refreshJobs();
    });
  } catch {
    message("会議の画面を読み込めませんでした。再読み込みしてください。", true);
  }
}
$("#join-form").onsubmit = join;
$("#record").onclick = () => {
  if (
    !recording &&
    !confirm("参加者に録画とAIによる解析を説明しましたか。録画を開始します。")
  )
    return;
  $("#record").disabled = true;
  jitsi.executeCommand(
    recording ? "stopRecording" : "startRecording",
    recording
      ? "file"
      : { mode: "file", shouldShare: false, extraMetadata: { ai_room: room } },
  );
  setTimeout(() => {
    $("#record").disabled = false;
  }, 90000);
};
$("#minutes").onclick = () =>
  roomApi("/minutes", { method: "POST" })
    .then(() => {
      message("議事録の作成を受け付けました。");
      refreshJobs();
    })
    .catch((e) => message(e.message, true));
$("#close-meeting").onclick = async () => {
  if (!confirm("AI処理を終了して、議事録を作成しますか。")) return;
  try {
    if (recording) jitsi?.executeCommand("stopRecording", "file");
    await roomApi("/close", { method: "POST" });
    closed = true;
    message(
      "AI処理を終了しました。会議はJitsiの操作で退出・終了してください。",
    );
    refreshJobs();
  } catch (e) {
    message(e.message, true);
  }
};
// 共有する参加URLには、主催者用の記録アクセス権限を含めない。
function copyConference() {
  navigator.clipboard
    .writeText(info.conference_url)
    .then(() => message("会議の参加リンクをコピーしました。"))
    .catch(() => message("通常の会議画面のリンクをコピーしてください。", true));
}
$("#copy-conference").onclick = copyConference;
$("#invite").onclick = copyConference;
for (const id of ["join-ios", "join-android"])
  $("#" + id).onclick = () => jitsi?.dispose();

// 招待権限を確認してから会議名、主催者操作、公式アプリ用リンクを表示する。
async function init() {
  try {
    info = await roomApi("");
    $("#meeting-title").textContent = info.title;
    document.title = info.title + " | Jitsi Meet";
    $("#name").value = sessionStorage.getItem("meeting-name") || "";
    $("#join-ios").href = info.ios_url;
    $("#join-android").href = info.android_url;
    $("#join-standard").href = info.conference_url;
    if (info.role === "host") {
      $("#host-tools").hidden = false;
      $("#invite").hidden = false;
      $("#jobs").dataset.host = "true";
    }
    $("#ai-status").textContent = info.configured
      ? "AIキー設定済み"
      : "AIキー設定待ち";
    $("#ai-status").classList.toggle("ok", info.configured);
    if (info.closed) {
      closed = true;
      $("#join-form").hidden = true;
      $("#ai-status").textContent = "会議は終了しています";
    }
    await refreshJobs();
  } catch (e) {
    $("#join-form").hidden = true;
    message(e.message, true);
  }
}
init();
setInterval(() => {
  if (!document.hidden) refreshJobs().catch(() => {});
}, 10000);
window.addEventListener("pagehide", () => jitsi?.dispose());

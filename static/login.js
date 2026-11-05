// File: static/login.js
// Summary: Submit administrator credentials without placing passwords in URLs or browser storage.
import { $, api, message } from "./shared.js";

// URLの値を画面へ反映せず、操作結果に対応した固定文だけを表示する。
const query = new URLSearchParams(window.location.search);
if (query.has("signed-out")) message("サインアウトしました。");
else if (query.has("expired")) message("管理者のサインインが必要です。もう一度サインインしてください。");

$("#login-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  const body = { username: $("#username").value.trim(), password: $("#password").value };
  $("#password").value = "";
  try {
    await api("/ai/api/admin/login", { method: "POST", body });
    window.location.replace("/ai/admin");
  } catch (error) {
    message(error.message, true);
    $("#password").focus();
  } finally {
    button.disabled = false;
  }
};

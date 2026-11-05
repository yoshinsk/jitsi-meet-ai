# 参照する上流ソフトウェア

このリポジトリはJitsi本体の再配布用ミラーではありません。サーバーでは公式パッケージと以下の上流ソースを利用しています。上流のライセンスと著作権表示は各配布物に保持してください。

| 対象 | 採用した版 | 参照先 |
| --- | --- | --- |
| Jitsi Meet | `stable/jitsi-meet_11248` | [リリース](https://github.com/jitsi/jitsi-meet/releases/tag/stable/jitsi-meet_11248)、[ライセンス](https://github.com/jitsi/jitsi-meet/blob/stable/jitsi-meet_11248/LICENSE) |
| Opus transcriber proxy | `ee5836e67806349395ede2447376003da893f7a9` | [ソース](https://github.com/jitsi/opus-transcriber-proxy/tree/ee5836e67806349395ede2447376003da893f7a9)、[ライセンス](https://github.com/jitsi/opus-transcriber-proxy/blob/ee5836e67806349395ede2447376003da893f7a9/LICENSE) |
| Jibri | 安定版パッケージ `8.0-235-g87bcd3c-1` | [公式リポジトリ](https://github.com/jitsi/jibri) |
| Node.js | 24系LTS | [公式配布](https://nodejs.org/en/download)、[ライセンス](https://github.com/nodejs/node/blob/v24.x/LICENSE) |

Python依存の版は `requirements.txt` に、公式プロキシの依存は採用コミットのロックファイルに記録しています。音声認識、音声通訳、字幕翻訳、議事録の外部送信先はOpenAI APIです。

日本語案内音声の台本と生成スクリプトはこのリポジトリに収録しています。試験時のWindows生成音声は、再配布条件を確認できていないため公開物に含めていません。利用者が用意する音声と音声エンジンの条件は、コードのApache License 2.0とは別に確認してください。

Chrome、ChromeDriver、Jibri、Node.js、Python依存などの外部プログラムは別途導入します。それぞれの配布物のライセンスと著作権表示を維持してください。このリポジトリのライセンスが、それらを一律に変更するものではありません。

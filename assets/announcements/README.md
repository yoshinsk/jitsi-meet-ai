# 日本語音声案内

公開リポジトリには生成済みの音声バイナリを含めていません。元の試験環境ではWindowsの `Microsoft Haruka Desktop` を使いましたが、生成音声の再配布条件は今回の公開作業では確認できていません。

日本語の台本と生成スクリプトはApache License 2.0で公開しています。利用する音声エンジン・音声データの条件を確認したうえで、音声を手元で用意してください。

Windowsに日本語音声がある場合は、リポジトリのルートで次を実行できます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ops/generate-japanese-announcements.ps1
```

生成する8個のWAVファイル名と台本は `ops/japanese-announcements.json` に定義しています。自分で録音した音声などを、同じファイル名で配置することもできます。

WAVがない場合、配置処理は既存のJitsi音声を維持します。一部だけ配置した場合は、置換を始める前にエラーにします。生成した音声はGitの公開対象から除外しています。

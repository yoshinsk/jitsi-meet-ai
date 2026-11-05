#!/usr/bin/env python3
# File: ops/patch-opus-proxy.py
# Summary: Adapt the pinned upstream OpenAI backend for automatic language detection and current live-transcription models.

from pathlib import Path
import subprocess


def main():
    # Preserve lazy codec loading in the Node bundle so a native-only installation does not load optional WASM files.
    build=Path('/opt/jitsi-opus-proxy/build.mjs')
    build_text=build.read_text()
    if 'splitting: true' not in build_text:
        build.write_text(build_text.replace("format: 'esm',", "format: 'esm',\n\tsplitting: true,"))
    target=Path('/opt/jitsi-opus-proxy/src/backends/OpenAIBackend.ts')
    text=target.read_text()
    if '// JITSI AI MODEL ADAPTER' in text:
        # A continuous RTP stream may never become transport-idle, so also commit bounded turns explicitly.
        text=text.replace("if ((this.backendConfig?.model || config.openai.model).startsWith('gpt-live-transcribe')) {\n\t\t\t\t\t\tthis.commitTimer = setInterval(() => this.forceCommit(), 4000);\n\t\t\t\t\t}",
                          "this.commitTimer = setInterval(() => this.forceCommit(), 4000);")
        text=text.replace("transcriptionConfig.model.startsWith('gpt-live-transcribe') ? null : getTurnDetectionConfig()",
                          "(transcriptionConfig.model.startsWith('gpt-live-transcribe') || transcriptionConfig.model.startsWith('gpt-transcribe')) ? null : getTurnDetectionConfig()")
        target.write_text(text)
        subprocess.run(['/opt/jitsi-node/bin/node','build.mjs'],cwd='/opt/jitsi-opus-proxy',check=True)
        return
    # Input-language hints from a participant's UI must not override the requested automatic source-language detection.
    original='\t\tif (this.backendConfig.language) {\n\t\t\ttranscriptionConfig.language = this.backendConfig.language;\n\t\t}'
    assert original in text
    text=text.replace(original,'\t\t// JITSI AI MODEL ADAPTER: omit source-language hints for automatic multilingual recognition.')
    text=text.replace("private lastTranscriptTime?: number;", "private lastTranscriptTime?: number;\n\tprivate commitTimer?: ReturnType<typeof setInterval>;")
    text=text.replace('this.sendSessionUpdate();\n\n\t\t\t\t\tresolve();',
                      "this.sendSessionUpdate();\n\t\t\t\t\tthis.commitTimer = setInterval(() => this.forceCommit(), 4000);\n\n\t\t\t\t\tresolve();")
    text=text.replace("close(): void {", "close(): void {\n\t\tif (this.commitTimer) clearInterval(this.commitTimer);\n\t\tthis.commitTimer = undefined;")
    text=text.replace("turn_detection: getTurnDetectionConfig(),", "turn_detection: (transcriptionConfig.model.startsWith('gpt-live-transcribe') || transcriptionConfig.model.startsWith('gpt-transcribe')) ? null : getTurnDetectionConfig(),")
    text=text.replace("include: ['item.input_audio_transcription.logprobs'],", "include: transcriptionConfig.model.startsWith('whisper') ? [] : ['item.input_audio_transcription.logprobs'],")
    text=text.replace('this.onCompleteTranscription?.(transcription);',
                      "if (parsedMessage.languages?.[0]?.code) transcription.language = parsedMessage.languages[0].code;\n\t\t\tthis.onCompleteTranscription?.(transcription);")
    target.write_text(text)
    subprocess.run(['/opt/jitsi-node/bin/node','build.mjs'],cwd='/opt/jitsi-opus-proxy',check=True)
    print('OPUS_MODEL_ADAPTER_APPLIED')


if __name__=='__main__':main()

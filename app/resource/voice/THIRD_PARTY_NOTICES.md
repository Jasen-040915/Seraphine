# Local speech recognition components

The Seraphine application modifications remain under GPLv3; see the root LICENSE.
The following separately supplied components retain their upstream notices.

- sherpa-onnx 1.13.8 and its core runtime: https://github.com/k2-fsa/sherpa-onnx (Apache-2.0).
- ONNX Runtime, included by sherpa-onnx: https://github.com/microsoft/onnxruntime (MIT).
- NumPy 2.2.6: https://numpy.org/ (BSD-3-Clause and bundled library notices in its distribution metadata).
- SenseVoiceSmall int8 model exported 2024-07-17 by the sherpa-onnx project:
  https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
  The model bundle's original LICENSE refers to the FunASR model license.
  Preserve models/sensevoice/LICENSE and FunASR-MODEL-LICENSE.txt with the model.
  SenseVoice project source: https://github.com/FunAudioLLM/SenseVoice;
  its current code license is included separately as SenseVoice-LICENSE.txt.

The application source ZIP excludes model weights. scripts/download_voice_model.py
downloads the original model release, verifies the release archive SHA256, and
retains the model notices. The application never uploads recorded audio.

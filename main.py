# coding:utf-8
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()

from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QTranslator
from app.common.qfluentwidgets import FluentTranslator
from qasync import QApplication, QEventLoop
import asyncio
import sys
from app.common.config import cfg, VERSION, BETA
from app.view.main_window import MainWindow


def main():
    args = sys.argv
    if len(args) == 2 and args[1] in ['--version', '-v']:
        print(BETA or VERSION)
        return

    instance = None
    if not any(flag in args for flag in ('--self-test', '--self-test-full')):
        from app.common.single_instance import SingleInstance
        instance = SingleInstance()
        if not instance.acquired:
            instance.request_activation()
            instance.close()
            return

    if cfg.get(cfg.dpiScale) == "Auto":
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    else:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(cfg.get(cfg.dpiScale))
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    font = QFont()
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferFullHinting)
    QApplication.setFont(font)

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    if instance is not None:
        app.aboutToQuit.connect(instance.close)

    eventLoop = QEventLoop(app)
    asyncio.set_event_loop(eventLoop)

    appCloseEvent = asyncio.Event()
    app.aboutToQuit.connect(appCloseEvent.set)

    locale = cfg.get(cfg.language).value
    fluentTranslator = FluentTranslator(locale)
    seraphineTranslator = QTranslator()
    seraphineTranslator.load(locale, "Seraphine", ".", "./app/resource/i18n")

    app.installTranslator(fluentTranslator)
    app.installTranslator(seraphineTranslator)

    if '--self-test' in args or '--self-test-full' in args:
        # Exercise the packaged Qt/resources without displaying windows or
        # starting any of the upstream client's automatic actions.
        import json
        import tempfile
        from pathlib import Path
        from PyQt5.QtGui import QFontDatabase
        for font_name in ('msyh.ttc', 'msyhbd.ttc'):
            font_path = Path(os.getenv('WINDIR', 'C:/Windows')) / 'Fonts' / font_name
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
        from app.overlay.panels import OverlayController, OverlaySettings
        from app.overlay.voice import VoiceSettings
        output = Path(os.getenv('APPDATA', '.')) / 'SeraphinePlus' / 'self-test'
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as profile:
            overlay = OverlayController(profile=profile, start_reader=False)
            overlay.timer.stop()
            overlay.preview = True
            # Diagnostics use sample data and never start background input services.
            overlay.economy.render(overlay.demo, overlay.catalog['items'], '自检预览 · ')
            overlay.dialog = OverlaySettings(overlay)
            overlay.economy.pass_through(True)
            result = {'success': overlay.economy.data.team_difference is not None,
                      'version': VERSION, 'catalog': overlay.catalog['version'],
                      'no_live_connection': not overlay.reader.isRunning()}
            result['background_services_not_started'] = (not overlay.voice.hook_started
                                                          and not overlay.mouse_started)
            voice_dialog = VoiceSettings(overlay.voice)
            result['voice_integrated'] = hasattr(overlay, 'voice') and not overlay.voice.worker.isRunning()
            if '--self-test-voice' in args:
                import time
                import wave
                from app.overlay.voice_audio import create_recognizer, decode_pcm, model_directory, SpeechActivity, LastDecodeCache, StreamingTranscript
                started = time.perf_counter()
                recognizer = create_recognizer()
                result['voice_model_load_seconds'] = round(time.perf_counter() - started, 3)
                with wave.open(str(model_directory() / 'test_wavs/zh.wav')) as wav:
                    rate, pcm = wav.getframerate(), wav.readframes(wav.getnframes())
                started = time.perf_counter()
                transcript = decode_pcm(recognizer, pcm, rate)
                result['voice_model'] = bool(transcript)
                result['voice_sample_text'] = transcript
                result['voice_decode_seconds'] = round(time.perf_counter() - started, 3)
                cache, cache_metrics = LastDecodeCache(), {}
                decode_pcm(recognizer, pcm, rate, cache=cache, session=1)
                reused = decode_pcm(recognizer, pcm + bytes(rate), rate, cache_metrics, cache=cache, session=1)
                result['voice_decode_cache'] = bool(reused == transcript and cache_metrics.get('cache_hit') and cache_metrics['decode_ms'] == 0)
                # Decode the bundled public sample with a simulated input sink.
                # Diagnostics never start microphone, keyboard or mouse monitoring.
                stream = StreamingTranscript()
                activity, previous, first_partial = SpeechActivity(rate), 0, None
                for seconds in (.8, 1.45, 2.1, 2.75, 3.4, 4.05, 4.7, 5.35):
                    end = min(len(pcm), int(seconds * rate) * 2)
                    activity.feed(pcm[previous:end]); previous = end
                    if activity.heard_voice and stream.update(decode_pcm(recognizer, pcm[:end], rate)):
                        stream.accept(stream.target)
                    if stream.committed and first_partial is None:
                        first_partial = seconds
                result['voice_streaming_before_end'] = first_partial is not None
                result['voice_streaming_first_sample_seconds'] = first_partial
                result['voice_streaming_final_matches'] = bool(stream.update(transcript, final=True)
                                                               and stream.target == transcript)

                class SampleInput:
                    def __init__(self): self.texts = []
                    def foreground(self, window): return window == 100
                    def modifiers_down(self): return False
                    def text(self, window, value):
                        self.texts.append(value)
                        return True
                controller, sample_input = overlay.voice, SampleInput()
                original_bridge = controller.bridge
                controller.bridge = sample_input
                controller.gate.context(True, True, 100)
                start = controller.gate.key(13, True, 100)
                controller.gate.key(13, False, 100)
                controller.partial_recognized(start.ticket, transcript, '')
                controller.gate.key(13, True, 100); controller.gate.key(13, False, 100)
                controller.recognized(start.ticket, transcript, '')
                from PyQt5.QtTest import QTest
                QTest.qWait(3000)
                result['voice_sample_input'] = ''.join(sample_input.texts) == transcript
                controller.cancel_delivery(); controller.gate.reset()
                controller.bridge = original_bridge
                result['voice_hardware_not_started'] = not controller.hook_started and not overlay.mouse_started
                result['no_microphone_recording'] = overlay.voice.audio.audio is None
            for name, widget in [('settings', overlay.dialog), ('economy', overlay.economy), ('voice-settings', voice_dialog)]:
                if name != 'economy':
                    widget.show()
                    app.processEvents()
                result[name] = widget.grab().save(str(output / (name + '.png')))
                widget.hide()
            result['voice_no_floating_window'] = not hasattr(overlay.voice, 'badge')
            voice_dialog.close()
            overlay.stop()
            if '--self-test-full' in args:
                w = MainWindow(smoke_test=True)
                w.overlay.timer.stop()
                # Offscreen rendering has no Windows Mica compositor.
                w.setMicaEffectEnabled(False)
                w.resize(1060, 850)
                w.ensurePolished()
                result['integrated_main_window'] = w.grab().save(str(output / 'main-window.png'))
                from app.common.qfluentwidgets import setTheme, Theme, qconfig
                from PyQt5.QtTest import QTest
                from app.overlay.panels import AlignmentDialog
                previous_theme = qconfig.theme
                for theme in (Theme.LIGHT, Theme.DARK):
                    setTheme(theme, save=False)
                    for name, page in [('economy-page', w.economyInterface), ('voice-page', w.voiceInterface)]:
                        w.stackedWidget.setCurrentWidget(page, popOut=False)
                        w.navigationInterface.setCurrentItem(page.objectName())
                        QTest.qWait(350)
                        page.verticalScrollBar().setValue(0)
                        app.processEvents()
                        key = name + '-' + theme.value.lower()
                        result[key] = w.grab().save(str(output / (key + '.png')))
                        page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                        app.processEvents()
                        result[key + '-bottom'] = w.grab().save(str(output / (key + '-bottom.png')))
                    alignment = AlignmentDialog(w.overlay)
                    alignment.adjustSize()
                    result['alignment-' + theme.value.lower()] = alignment.grab().save(str(output / ('alignment-' + theme.value.lower() + '.png')))
                    alignment.close()
                setTheme(previous_theme, save=False)
                result['single_program_overlay'] = hasattr(w, 'overlay') and not hasattr(w.overlay, 'spells')
                w.overlay.stop()
                w.trayIcon.hide()
                w.opggWindow.close()
                sys.excepthook = w.oldHook
                w.hide()
                w.deleteLater()
            (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        eventLoop.close()
        return

    if '--overlay-only' in args:
        from app.overlay.panels import OverlayController
        overlay = OverlayController()
        overlay.show_settings()
        instance.watch(app, overlay.show_settings)
        eventLoop.run_until_complete(appCloseEvent.wait())
        eventLoop.close()
        return

    w = MainWindow()
    w.show()
    instance.watch(app, w.activate_main_window)

    eventLoop.run_until_complete(appCloseEvent.wait())
    eventLoop.close()


if __name__ == '__main__':
    main()

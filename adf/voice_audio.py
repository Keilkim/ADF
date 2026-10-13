"""Qt microphone input, real input level, and in-memory WAV playback."""
from __future__ import annotations

import io
import time
import wave

import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QAudioSource, QMediaDevices, QtAudio

from .voice_notes import MAX_AUDIO_BYTES


def pcm_samples(data, sample_format):
    formats = {QAudioFormat.SampleFormat.UInt8: ('u1', 128., 128.),
               QAudioFormat.SampleFormat.Int16: ('<i2', 0., 32768.),
               QAudioFormat.SampleFormat.Int32: ('<i4', 0., 2147483648.),
               QAudioFormat.SampleFormat.Float: ('<f4', 0., 1.)}
    dtype, offset, scale = formats[sample_format]
    width = np.dtype(dtype).itemsize
    values = np.frombuffer(data[:len(data) // width * width], dtype=dtype)
    return (values.astype(np.float32) - offset) / scale


class VoiceRecorder(QObject):
    levelChanged = Signal(float, float)
    failed = Signal(str)
    limitReached = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.input = None
        self.data = bytearray()
        self.format = None
        self.started = 0.
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.meter)
        self.level = 0.
        self.limit_fired = False

    def start(self):
        self.cancel()
        self.limit_fired = False
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise ValueError('마이크를 찾을 수 없습니다. 마이크 연결과 접근 권한을 확인해 주세요.')
        fmt = QAudioFormat()
        fmt.setSampleRate(16000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        if fmt.sampleFormat() not in (QAudioFormat.SampleFormat.UInt8, QAudioFormat.SampleFormat.Int16,
                                     QAudioFormat.SampleFormat.Int32, QAudioFormat.SampleFormat.Float):
            raise ValueError('이 마이크의 녹음 형식을 지원하지 않습니다.')
        self.format = fmt
        self.source = QAudioSource(device, fmt, self)
        self.source.setBufferSize(max(4096, fmt.bytesForDuration(100000)))
        self.input = self.source.start()
        if self.input is None or self.source.error() != QtAudio.Error.NoError:
            self.cancel()
            raise ValueError('마이크를 켤 수 없습니다. Windows/macOS의 마이크 접근 권한을 확인해 주세요.')
        self.input.readyRead.connect(self.read)
        self.source.stateChanged.connect(self.state_changed)
        self.started = time.monotonic()
        self.timer.start()

    def read(self):
        if self.input is None:
            return
        chunk = bytes(self.input.readAll())
        self.data.extend(chunk)
        values = pcm_samples(chunk, self.format.sampleFormat())
        if len(values):
            self.level = min(1., float(np.sqrt(np.mean(values * values))) * 4)
        if not self.limit_fired and (len(self.data) >= MAX_AUDIO_BYTES - 4096 or time.monotonic() - self.started >= 600):
            self.limit_fired = True
            self.limitReached.emit()

    def meter(self):
        self.levelChanged.emit(self.level, time.monotonic() - self.started)
        self.level *= .8

    def state_changed(self, state):
        if self.source and state == QtAudio.State.StoppedState and self.source.error() != QtAudio.Error.NoError:
            self.failed.emit('마이크 연결이 끊어졌습니다. 연결과 권한을 확인해 주세요.')

    def finish(self):
        if self.source is None:
            return b'', 0.
        self.read()
        fmt = self.format
        data = bytes(self.data)
        self.cancel()
        values = pcm_samples(data, fmt.sampleFormat())
        frames = len(values) // fmt.channelCount()
        if frames:
            values = values[:frames * fmt.channelCount()].reshape(frames, fmt.channelCount()).mean(axis=1)
        else:
            values = np.array([], dtype=np.float32)
        sample_rate = 16000
        if len(values) and fmt.sampleRate() != sample_rate:
            count = round(len(values) * sample_rate / fmt.sampleRate())
            values = np.interp(np.arange(count) * fmt.sampleRate() / sample_rate, np.arange(len(values)), values)
        pcm = (np.clip(values, -1., 1.) * 32767).astype('<i2').tobytes()
        output = io.BytesIO()
        with wave.open(output, 'wb') as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(sample_rate)
            recording.writeframes(pcm)
        return output.getvalue(), frames / fmt.sampleRate()

    def cancel(self):
        self.timer.stop()
        source, self.source = self.source, None
        self.input = None
        if source is not None:
            source.stop()
            source.deleteLater()
        self.data.clear()
        self.level = 0.


class VoicePlayer(QObject):
    playingChanged = Signal(bool)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sink = None
        self.buffer = None

    @property
    def playing(self):
        return self.sink is not None

    def play(self, audio):
        self.stop()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise ValueError('재생할 스피커나 이어폰을 찾을 수 없습니다.')
        with wave.open(io.BytesIO(audio), 'rb') as recording:
            if recording.getsampwidth() != 2:
                raise ValueError('지원하지 않는 녹음 형식입니다.')
            fmt = QAudioFormat()
            fmt.setSampleRate(recording.getframerate())
            fmt.setChannelCount(recording.getnchannels())
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            pcm = recording.readframes(recording.getnframes())
        # Resample to the output device's preferred rate when it rejects the input.
        if not device.isFormatSupported(fmt):
            preferred = device.preferredFormat()
            values = np.frombuffer(pcm, '<i2').astype(np.float32).reshape(-1, fmt.channelCount()).mean(axis=1) / 32768.
            count = round(len(values) * preferred.sampleRate() / fmt.sampleRate())
            values = np.interp(np.arange(count) * fmt.sampleRate() / preferred.sampleRate(), np.arange(len(values)), values)
            values = np.repeat(values[:, None], preferred.channelCount(), axis=1)
            converters = {QAudioFormat.SampleFormat.UInt8: lambda v: ((v+1)*127.5).clip(0,255).astype('u1'),
                          QAudioFormat.SampleFormat.Int16: lambda v: (v*32767).astype('<i2'),
                          QAudioFormat.SampleFormat.Int32: lambda v: (v*2147483647).astype('<i4'),
                          QAudioFormat.SampleFormat.Float: lambda v: v.astype('<f4')}
            pcm = converters[preferred.sampleFormat()](values).tobytes()
            fmt = preferred
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(pcm))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.sink = QAudioSink(device, fmt, self)
        self.sink.stateChanged.connect(self.state_changed)
        self.sink.start(self.buffer)
        if self.sink.error() != QtAudio.Error.NoError:
            self.stop()
            raise ValueError('녹음을 재생할 수 없습니다. 출력 장치를 확인해 주세요.')
        self.playingChanged.emit(True)

    def state_changed(self, state):
        if state == QtAudio.State.IdleState:
            self.stop()
        elif state == QtAudio.State.StoppedState and self.sink and self.sink.error() != QtAudio.Error.NoError:
            self.stop()
            self.failed.emit('오디오 재생이 중단되었습니다.')

    def stop(self):
        sink, self.sink = self.sink, None
        if sink:
            sink.stop()
            sink.deleteLater()
        if self.buffer:
            self.buffer.close()
            self.buffer.deleteLater()
            self.buffer = None
        self.playingChanged.emit(False)

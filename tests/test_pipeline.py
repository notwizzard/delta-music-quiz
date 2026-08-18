"""Проверки пайплайна на синтетическом материале с известным ответом."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from dmq import analysis as an
from dmq import audio as au
from dmq import mashup as mx
from dmq import presets as ps
from dmq import transforms as tr

SR = au.SR


def low_envelope(audio: np.ndarray) -> np.ndarray:
    """Огибающая низов — практически чистая бочка. По ней и сверяем удары."""
    sos = butter(4, 200, btype="low", fs=SR, output="sos")
    return np.abs(sosfilt(sos, au.to_mono(audio)))


# --- анализ -----------------------------------------------------------------

@pytest.mark.parametrize("name", ["alpha", "beta", "gamma"])
def test_tempo_detected_in_right_octave(tracks, name):
    """Темп определяется верно, а не вдвое медленнее — это самая частая ошибка."""
    path, expected_bpm = tracks[name]
    result = an.analyze(str(path))
    assert result.bpm == pytest.approx(expected_bpm, rel=0.02)


@pytest.mark.parametrize("name", ["alpha", "beta", "gamma"])
def test_downbeats_land_on_bar_starts(tracks, name):
    """Найденные сильные доли совпадают с настоящими началами тактов."""
    path, bpm = tracks[name]
    result = an.analyze(str(path))
    bar = 4 * 60.0 / bpm

    for downbeat in result.downbeats[:8]:
        offset = downbeat % bar
        distance = min(offset, bar - offset)
        assert distance < 0.05, f"сильная доля в {downbeat:.3f} с мимо сетки на {distance * 1000:.0f} мс"


@pytest.mark.parametrize("name", ["alpha", "beta", "gamma"])
def test_downbeat_phase_is_confident(tracks, name):
    """На материале с явной бочкой фаза такта должна определяться уверенно."""
    path, _ = tracks[name]
    assert not an.analyze(str(path)).downbeats_are_shaky


def test_key_detection_finds_tonic_or_relative(tracks):
    """Тоника определяется точно или как параллельная — они неразличимы по нотам."""
    result = an.analyze(str(tracks["beta"][0]))
    assert result.key_name in {"G", "Em"}


# --- простые преобразования -------------------------------------------------

@pytest.mark.parametrize(
    ("preset", "ratio"),
    [
        ("speed_125", 1 / 1.25),
        ("speed_150", 1 / 1.5),
        ("speed_200", 1 / 2.0),
        ("slow_150", 1.5),
        ("slow_200", 2.0),
        ("slow_400", 4.0),
        ("reverse", 1.0),
        ("pitch_up_5", 1.0),
        ("pitch_down_5", 1.0),
    ],
)
def test_preset_duration(tracks, preset, ratio):
    """Скорость меняет длину ровно во столько раз, во сколько обещано, а питч — не меняет."""
    audio = au.slice_seconds(au.load(str(tracks["alpha"][0])), 0, 8.0)
    result = ps.apply(preset, audio)
    assert au.duration(result) == pytest.approx(8.0 * ratio, rel=0.015)


@pytest.mark.parametrize("semitones", [-7, -5, -3, 3, 5, 7])
def test_pitch_shift_moves_the_right_amount(pure_tone, semitones):
    """Сдвиг на N полутонов умножает частоту ровно на 2^(N/12)."""
    shifted = tr.pitch(pure_tone, SR, semitones)
    assert _peak_hz(shifted) == pytest.approx(440.0 * 2 ** (semitones / 12), rel=0.01)


def test_pitch_shift_keeps_duration(pure_tone):
    """Питч не должен трогать длину — иначе это уже другое преобразование."""
    assert au.duration(tr.pitch(pure_tone, SR, 5)) == pytest.approx(4.0, rel=0.01)


def _peak_hz(audio: np.ndarray) -> float:
    mono = au.to_mono(audio)
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(mono.size)))
    frequencies = np.fft.rfftfreq(mono.size, 1 / SR)
    return float(frequencies[np.argmax(spectrum)])


def test_reverse_is_exact_mirror(tracks):
    audio = au.slice_seconds(au.load(str(tracks["alpha"][0])), 0, 3.0)
    assert np.array_equal(tr.reverse(audio), audio[:, ::-1])


# --- наложение --------------------------------------------------------------

def test_mashup_layers_share_one_beat(tracks):
    """Главная проверка: удары всех слоёв приходятся на одно и то же время.

    Считаем взаимную корреляцию огибающих низов. Если бит общий, пик корреляции
    стоит на нулевой задержке; уехавший слой сдвинул бы его на доли секунды.
    """
    sources = [str(tracks[name][0]) for name in ("alpha", "beta", "gamma")]
    result = mx.mashup(sources, bars=4)

    beat = 60.0 / result.bpm
    envelopes = [low_envelope(layer.audio) for layer in result.layers]

    for i in range(len(envelopes)):
        for j in range(i + 1, len(envelopes)):
            lag = _best_lag(envelopes[i], envelopes[j], max_lag=int(0.25 * SR))
            assert abs(lag) < 0.05 * beat, (
                f"{result.layers[i].name} и {result.layers[j].name} разъехались "
                f"на {lag * 1000:.0f} мс при доле {beat * 1000:.0f} мс"
            )


def _best_lag(first: np.ndarray, second: np.ndarray, max_lag: int) -> float:
    a, b = first - first.mean(), second - second.mean()
    correlation = np.correlate(a, b, mode="full")
    lags = np.arange(-a.size + 1, a.size)
    window = np.abs(lags) <= max_lag
    return float(lags[window][np.argmax(correlation[window])] / SR)


def test_mashup_layers_have_identical_length(tracks):
    """Слои обязаны быть одной длины, иначе на последнем такте всё поедет."""
    sources = [str(tracks[name][0]) for name in ("alpha", "beta")]
    result = mx.mashup(sources, bars=4)

    lengths = {layer.audio.shape[1] for layer in result.layers}
    assert len(lengths) == 1

    expected = 4 * 4 * 60.0 / result.bpm
    assert au.duration(result.audio) == pytest.approx(expected, rel=0.01)


def test_mashup_matches_keys_when_asked(tracks):
    """С включённым подбором тональности слои сдвигаются к тонике первого трека."""
    sources = [str(tracks[name][0]) for name in ("alpha", "beta")]

    matched = mx.mashup(sources, bars=4, match_key=True)
    assert any(layer.semitones != 0 for layer in matched.layers)

    raw = mx.mashup(sources, bars=4, match_key=False)
    assert all(layer.semitones == 0 for layer in raw.layers)


def test_mashup_does_not_clip(tracks):
    """Сумма слоёв не должна уходить в перегруз."""
    sources = [str(tracks[name][0]) for name in ("alpha", "beta", "gamma")]
    result = mx.mashup(sources, bars=4)
    assert np.max(np.abs(result.audio)) <= 1.0


def test_tempo_octave_folding():
    """Половинный и двойной темп сводятся к одному человеческому диапазону."""
    assert an._tempo_factor(60.0) == 2.0
    assert an._tempo_factor(240.0) == 0.5
    assert an._tempo_factor(120.0) == 1.0


def test_bpm_folding_against_reference():
    """85 и 170 — это один и тот же бит, слои не должны разъезжаться вдвое."""
    assert mx._fold_bpm(85.0, 170.0) == pytest.approx(170.0)
    assert mx._fold_bpm(170.0, 85.0) == pytest.approx(85.0)
    assert mx._fold_bpm(128.0, 130.0) == pytest.approx(128.0)

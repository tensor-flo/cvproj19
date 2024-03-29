import multisensory.aolib.util as ut
import multisensory.aolib.sound as sound
from matplotlib.pyplot import imshow
from PIL import Image
import numpy as np
import scipy.signal
import IPython.display as ipd
import torchvision
import torch
from torch import nn

def subbands_from_sound(signal, filts):
    ft_signal = np.fft.rfft(signal)
    ft_subbands = filts * ft_signal[:, np.newaxis]
    subbands = np.fft.irfft(ft_subbands, axis = 0)
    return subbands.real

def sound_from_subbands(subbands, filts):
    ft_subbands = np.fft.rfft(subbands, axis = 0)
    ft = filts * ft_subbands
    ift = np.fft.irfft(ft, axis = 0)
    return np.sum(ift, axis = 1)


def freq2erb(freq_hz):
    return 9.265*np.log(1+freq_hz/(24.7*9.265))

def erb2freq(n_erb):
    return 24.7*9.265*(np.exp(n_erb/9.265)-1)

def rfftfreq(n, d=1.0):
    if not (isinstance(n,int) or isinstance(n, np.integer)):
        raise ValueError("n should be an integer")
    val = 1.0/(n*d)
    N = n//2 + 1
    results = np.arange(0, N, dtype=np.int)
    return results * val


def erb_filters(length, sr, nbands, low_lim, hi_lim):
    length = int(length)
    sr = float(sr)
    max_freq = sr/2.
    #freqs = np.fft.fftfreq(length, 1./sr)
    #freqs = freqs[freqs >= 0]
    freqs = rfftfreq(length, 1./sr)
    nfreqs = len(freqs)

    if hi_lim > sr/2:
        hi_lim = max_freq
  
    cos_filts = np.zeros((nfreqs, nbands))
    cutoffs = erb2freq(np.linspace(freq2erb(low_lim), freq2erb(hi_lim), 2 + nbands))
    for k in range(nbands):
        # adjacent filters overlap by 50%
        l = cutoffs[k]
        h = cutoffs[k+2]
        l_ind = np.flatnonzero(freqs > l)[0]
        h_ind = np.flatnonzero(freqs < h)[-1]
        avg = 0.5*(freq2erb(l) + freq2erb(h))
        rnge = freq2erb(h) - freq2erb(l)
        # Map cutoffs from -pi/2 to pi/2 interval (then to a kind of circular filter, although the value of each
        # filter component corresponds to its distance in ERB space to the average ERB value, so it's kind of nonlinear)
        cos_filts[l_ind : h_ind+1, k] = np.cos(((freq2erb(freqs[l_ind : h_ind+1]) - avg) / rnge) * np.pi)

    filts = np.zeros((nfreqs, nbands + 2))
    filts[:, 1:nbands+1] = cos_filts
    h_ind = np.flatnonzero(freqs < cutoffs[1])[-1]
    # lowpass filter goes to the peak of first cos filter 
    filts[:h_ind+1, 0] = np.sqrt(1 - filts[:h_ind+1, 1]**2)
    l_ind = np.flatnonzero(freqs > cutoffs[nbands])[0]
    filts[l_ind:, nbands+1] = np.sqrt(1 - filts[l_ind:, nbands]**2)

    return filts, cutoffs, freqs

def resample(signal, sc, clip = True, num_samples = None):
    n = int(round(signal.shape[0]/sc)) if num_samples is None else num_samples
    r = scipy.signal.resample(signal, n)

    if clip:
        r = np.clip(r, -1, 1)
    return r


def good_resample_length(n, new_sr, old_sr, factor2 = 100):
    factor = new_sr / float(old_sr)
    new_len = int(np.floor(n / factor) * factor)
    return (new_len - (new_len%factor2))

def cut_signal(signal, new_sr, old_sr):
    new_signal = signal[: good_resample_length(signal.shape[0], new_sr, old_sr)]
    return new_signal

def invert_subband_envs(target_envs, final_sr, env_sr, mid_sr = 20000, niters = 3, low_lim = 20, hi_lim = 10000, comp_exp = 0.3):
    # number of subbands (w/o high/low pass)
    nbands = target_envs.shape[1]-2
    snd_len = int(np.ceil(target_envs.shape[0] * float(mid_sr) / float(env_sr)))
    snd_len -= (snd_len % 100)
    target_envs = np.clip(target_envs, 0, 1)

    #snd_len = good_resample_length(target_envs.shape[0], mid_sr, env_sr, 10)
    filts, _, _ = erb_filters(snd_len, mid_sr, nbands, low_lim, hi_lim)
    #cut_signal(signal, new_sr, old_sr):
    synth_sound = np.random.randn(snd_len)
    for i in range(niters):
        # Forward pass: current sound -> downsampled envelopes and full-res phases
        synth_subbands = subbands_from_sound(synth_sound, filts)
        analytic = scipy.signal.hilbert(synth_subbands, axis = 0)
        synth_envs = np.abs(analytic)
        phases = analytic / synth_envs

        #up_target_envs = resample(target_envs, mid_sr / env_sr)
        up_target_envs = scipy.signal.resample(target_envs, phases.shape[0])
        up_target_envs = np.maximum(up_target_envs, 0.)
        up_target_envs **= (1./comp_exp)

        new_analytic = phases * up_target_envs
        synth_subbands = np.real(new_analytic)
        synth_sound = sound_from_subbands(synth_subbands, filts)
    
    synth_sound = resample(synth_sound, final_sr / float(mid_sr))
    synth_sound = np.clip(synth_sound, -1., 1.)
    return sound.Sound(None, final_sr, synth_sound)


def subband_envs(snd, nbands, env_sr, num_samples = None, ds_sr = 20000, low_lim = 20, hi_lim = 10000, comp_exp = 0.3, desired_rms = None, clip_samples = True):
    ds_sr = float(ds_sr)
    signal0, sr0 = snd.normalized().to_mono().samples.flatten(), snd.rate
    if desired_rms is not None:
        est_rms = max(1e-5, np.sqrt(np.mean(np.asarray(signal0)**2)))
        signal0 = signal0 * (desired_rms / est_rms)
    
    sr0 = float(sr0) #=>snd.rate (fs)
    signal0 = cut_signal(signal0, ds_sr, sr0)
    signal = resample(signal0, ds_sr / sr0, clip = clip_samples)

    # number of samples in the signal that correspond to one subband envelope feature
    ds_factor = int(np.floor(ds_sr / env_sr))
    signal = signal[:int((len(signal)//ds_factor) * ds_factor)]
    audio_filts, Hz_cutoffs, freqs = erb_filters(len(signal), sr0, nbands, low_lim, hi_lim) # consistent w/ matlab
    subbands = subbands_from_sound(signal, audio_filts)
    analytic_subbands = scipy.signal.hilbert(subbands, axis = 0) 
    subband_envs = np.abs(analytic_subbands)
    subband_envs **= comp_exp
    ds_factor = ds_sr/env_sr
    subband_envs = scipy.signal.resample(subband_envs, int(subband_envs.shape[0]/ds_factor))
    return subband_envs

def mod_filters(length, sr, num_mod_channels = 10, low_lim = 0.5, hi_lim = 200., circular_conv = True, q = 1.):
    """ Modulation filters. These are similar to the cochlear filters
    (make_erb_cos_filters), but they're applied to subband envs, rather
    than to the raw waveform, and so they are tuned to lower
    frequencies."""
    # see make_constQ_cos_filters.m
    max_freq = sr/2.
    freqs = rfftfreq(length, 1./sr)
    nfreqs = len(freqs)
    if hi_lim > sr/2:
        hi_lim = max_freq

  # center frequencies evenly spaced on log scale
    cos_filts = np.zeros((nfreqs, num_mod_channels))
    cfs = 2**np.linspace(np.log2(low_lim), np.log2(hi_lim), num_mod_channels)
    for k in range(num_mod_channels):
        # increase the width of the filter as a function of frequency
        bw = cfs[k]/q
        l = cfs[k] - bw
        h = cfs[k] + bw
        freq_ok = np.flatnonzero((l < freqs) & (freqs < h))
        l_ind, h_ind = freq_ok[0], 1+freq_ok[-1]
        avg = cfs[k]
        rnge = h - l
        # go from theta = -pi/2 to pi/2 
        filt = np.cos(((freqs[l_ind : h_ind] - avg)/rnge)*np.pi)
        cos_filts[l_ind : h_ind, k] = filt

    # the filters won't sum to 1 (not sure why this only applies to the middle of the spectrum)
    # this probably doesn't matter as much for us, since we're not synthesizing sound
    energy = np.sum(cos_filts**2, axis = 1)
    energy_middle = energy[(freqs >= cfs[3]) & (freqs <= cfs[-4])]
    filts = cos_filts / np.sqrt(energy_middle.mean())
    return filts

def mod_power(envs, sr):
    filts = mod_filters(envs.shape[0], sr)
    mod_subbands = []
    for j in range(envs.shape[1]):
        mod_subbands.append(subbands_from_sound(envs[:, j], filts))
    return np.array(mod_subbands).transpose((1,0,2))

def texture_stats(envs, sr, normalize_envs = False):
    # marginal subband stats
    # not using a windowing function
    envs = envs.astype('float64')
    if normalize_envs:
        loudness = np.median(np.sqrt(np.sum(envs**2, axis = 1)))
        envs = envs / loudness
        #assert 0
    else:
        loudness = np.array(0.)

    eps = np.finfo(np.float32).eps
    env_mu = envs.mean(0)
    env_var_unnorm = envs.var(0)
    env_var_norm = env_var_unnorm / (eps + env_mu**2)
    env_stdev_norm = np.sqrt(env_var_norm)

    pairs = [1, 2, 3, 5]
    num_bands = envs.shape[1]
    corrs = []


    for i in range(num_bands):
        for j in pairs:
            ii = i + j
            if ii < num_bands:
                corrs.append(np.corrcoef(envs[:, i], envs[:, ii])[0, 1])
        
    corrs = np.array(corrs)
    # when a band has zero variance, this can happen
    corrs[np.isnan(corrs)] = 0.

    mods = mod_power(envs, sr)
    mod_pow_norm = np.zeros(mods.shape[1:])
    for j in range(num_bands):
        mod_pow_unnorm = np.mean(mods[:, j]**2, axis = 0)
        mod_pow_norm[j, :] = np.sqrt(mod_pow_unnorm / (eps + env_var_unnorm[j]))

    for x in [env_mu, env_stdev_norm, corrs, mod_pow_norm, loudness]:
        assert not np.any(np.isnan(x))
        assert not np.any(np.isinf(x))
    
    return env_mu, env_stdev_norm, corrs, mod_pow_norm, loudness
    
import os
    
def extract_textures(audio_dir):
    #Magic numbers
    fps = 29.97
    samp_sr = 21000.
    frame_dur = 1/fps
    shift_dur = 4.2
    ds_sr = 20000
    env_sr = 400
    #result_array = np.empty((0, 502))
    for filename in os.listdir(audio_dir):
        if filename.endswith(".wav"):
            print(filename)
            snd = sound.load_sound(os.path.join(audio_dir, filename)).normalized()
            sub_envs = subband_envs(snd, nbands=30, ds_sr=ds_sr, env_sr=env_sr)
            a = texture_stats(sub_envs, sr=env_sr)
            sound_textures = np.concatenate((a[0], a[1], a[2], a[3].ravel(), np.atleast_1d(a[4])), axis=0)
            np.save('./dataset/features/'+filename+'.npy', sound_textures)

extract_textures('./dataset/audio')

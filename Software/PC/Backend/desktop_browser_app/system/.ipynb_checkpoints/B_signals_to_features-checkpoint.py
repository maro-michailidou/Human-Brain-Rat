import numpy as np
import zmq
import json
from scipy.signal import find_peaks, hilbert, welch
from scipy.fft import fft, fftfreq
import itertools
from scipy.spatial.distance import euclidean
from fastdtw import fastdtw
import logging

# Constants
NUM_CHANNELS = 32
FS = 500  # Sampling rate in Hz
UPDATE_RATE = 1  # Update rate in Hz
BUFFER_SIZE = int(FS // UPDATE_RATE)  # Size of buffer corresponding to update rate

def receive_neural_data(socket):
    try:
        message = socket.recv()  # Receive the message
        ecog_data = json.loads(message.decode('utf-8'))  # Decode and load the JSON data
        neural_data = np.array(ecog_data, dtype=np.float32)  # Convert the list to a NumPy array
        return neural_data
    except zmq.ZMQError as e:
        print(f"Error receiving data: {e}")
        return None

def buffer_data(data, buffer):
    buffer = np.roll(buffer, -data.shape[0], axis=1)
    buffer[:, -data.shape[0]:] = data
    return buffer

def scale_data(data):
    return data / 255.0


def detect_peak_heights(signals, height=None, threshold=None, distance=None, prominence=None):
    # Initialize an empty list to store average peak heights
    average_heights = []

    # Loop through each signal
    for signal in signals:
        # Use find_peaks with additional criteria for more selective peak detection
        peaks, properties = find_peaks(signal, height=height, threshold=threshold, distance=distance, prominence=prominence)
        
        # Calculate the average height of the detected peaks
        if peaks.size > 0:
            average_height = np.mean(properties["peak_heights"])
        else:
            average_height = 0
        
        average_heights.append(average_height)
    
    return np.array(average_heights)
    
def detect_peaks(signals, height=None, distance=None, prominence=None):
    peak_counts = np.zeros(signals.shape[0], dtype=int)  # Pre-allocate peak_counts array

    for i, signal in enumerate(signals):
        peaks, _ = find_peaks(signal, height=height, distance=distance, prominence=prominence)
        peak_counts[i] = len(peaks)
    
    return peak_counts

def calculate_variance_std_dev(signals):
    # Calculating variance and standard deviation
    variance = np.var(signals, axis=1) 
    std_dev = np.std(signals, axis=1)
    return variance, std_dev

def calculate_rms(signals): 
    # Calculating RMS value
    rms = np.sqrt(np.mean(signals**2, axis=1))
    return rms

def freq_bands(signals, fs=FS):
    # Assuming signals is a 2D array: channels x samples
    band_features = np.zeros((signals.shape[0], 4))  # 4 frequency bands
    for i, signal in enumerate(signals):
        # Decrease sampling rate and adjust number of samples
        downsampled_signal = signal[::2]  # Downsample by 2 (adjust factor as needed)
        adjusted_fs = fs // 2  # Adjusted sampling rate
        
        # Adjust number of samples for Welch's method
        nperseg = min(32, len(downsampled_signal))  # Use minimum of 32 samples or signal length
        
        frequencies, psd = welch(downsampled_signal, fs=adjusted_fs, nperseg=nperseg)  # Adjusted nperseg
        
        # Frequency bands
        bands = {'delta': (1, 4), 'theta': (4, 8), 'alpha': (8, 13), 'beta': (13, 30)}
        for j, (name, (low, high)) in enumerate(bands.items()):
            idx = np.logical_and(frequencies >= low, frequencies <= high)
            if np.any(idx):  # Check if any frequencies fall within the band
                band_features[i, j] = np.nanmean(psd[idx])
            else:
                # Assign a default value if the band is empty
                band_features[i, j] = 0.0  # Or any other suitable default value
    return band_features


def calculate_spectral_entropy(neural_data_array, neural_channels, fs=FS):  
    spectral_entropy_dict = {}
    for channel, signal in zip(neural_channels, neural_data_array):
        # Determine nperseg based on the length of the signal
        nperseg = min(len(signal), fs * 2)
        # Calculate the power spectral density (PSD) using Welch's method
        frequencies, psd = welch(signal, fs=fs, nperseg=nperseg)
        
        # Check if the sum of psd is zero
        if np.sum(psd) == 0:
            spectral_entropy_dict[channel] = 0.0  # Set spectral entropy to 0
        else:
            # Normalize the PSD to get a probability distribution for entropy calculation
            normalized_psd = psd / np.sum(psd)
            # Calculate the spectral entropy
            spectral_entropy = -np.sum(normalized_psd * np.log2(normalized_psd))
            # Store the spectral entropy in the dictionary with the channel name as the key
            spectral_entropy_dict[channel] = float(spectral_entropy)  # Convert to Python float
    return spectral_entropy_dict


def spectral_centroids(signals, fs=FS): 
    # Calculate the spectral centroids for each signal
    centroids = []
    for signal in signals:
        fft_result = fft(signal)
        frequencies = fftfreq(len(signal), 1.0/fs)
        magnitude = np.abs(fft_result)
        centroid = np.sum(frequencies * magnitude) / np.sum(magnitude)
        centroids.append(centroid)
    return np.array(centroids)

def spectral_edge_density(signals, fs=FS, percentage=95):
    # Loop through each channel and calculate spectral edge density
    spectral_edge_densities = []
    for signal in signals:
        fft_result = fft(signal)
        frequencies = fftfreq(len(signal), 1.0/fs)
        positive_frequencies = frequencies[frequencies >= 0]
        positive_fft_result = fft_result[frequencies >= 0]
        magnitude = np.abs(positive_fft_result)
        sorted_magnitude = np.sort(magnitude)[::-1]
        cumulative_sum = np.cumsum(sorted_magnitude)
        total_power = np.sum(magnitude)
        threshold = total_power * (percentage / 100)
        spectral_edge = positive_frequencies[np.argmax(cumulative_sum >= threshold)]
        spectral_edge_densities.append(spectral_edge)
    return np.array(spectral_edge_densities)


def phase_locking_values(signal1, signal2):
    # Compute the analytical signal for each input signal
    num_signals = signals.shape[0]
    plv_matrix = np.zeros((num_signals, num_signals))

    for i in range(num_signals):
        for j in range(i+1, num_signals):  # Only compute for unique pairs
            signal1 = signals[i]
            signal2 = signals[j]
            
            # Compute the analytical signal for each input signal
            analytic_signal1 = hilbert(signal1)
            analytic_signal2 = hilbert(signal2)
            
            # Compute the instantaneous phase for each analytical signal
            phase1 = np.angle(analytic_signal1)
            phase2 = np.angle(analytic_signal2)
            
            # Compute the phase difference
            phase_diff = phase1 - phase2
            
            # Compute the PLV
            plv = np.abs(np.sum(np.exp(1j * phase_diff)) / len(signal1))
            
            # Store the PLV in the matrix
            plv_matrix[i, j] = plv
            plv_matrix[j, i] = plv  # PLV is symmetric

    return plv_matrix



def calculate_higuchi_fractal_dimension(signals, k_max):
    hfd_values = []  # To store HFD for each channel
    
    for signal in signals:
        N = len(signal)
        L = []
        x = np.asarray(signal)
        for k in range(1, k_max + 1):
            Lk = []
            for m in range(k):
                Lkm_sum = 0
                max_index = int((N - m - 1) / k) + 1  # Ensures valid indexing
                for i in range(1, max_index):
                    Lkm_sum += abs(x[m + i*k] - x[m + (i-1)*k])
                if max_index - 1 > 0:  # Check to avoid division by zero
                    Lkm = Lkm_sum * (N - 1) / (k * (max_index - 1))
                    Lk.append(Lkm)
                else:
                    Lk.append(0)  # Or handle appropriately
                
            # Check if Lk contains non-zero values before taking log
            if np.mean(Lk) > 0:
                L.append(np.log(np.mean(Lk)))
            else:
                # Handle the case where np.mean(Lk) results in 0 or a negative number
                # For example, append a very small positive value or handle as an error
                L.append(np.log(np.finfo(float).eps))  # Using machine epsilon for a small positive number

        # Proceed with polyfit only if L contains valid log values
        if len(L) > 0 and np.all(np.isfinite(L)):
            hfd = np.polyfit(np.log(range(1, k_max + 1)), L, 1)[0]  # Linear fit to log-log plot
            hfd_values.append(hfd)
        else:
            # Handle the case where HFD cannot be computed due to invalid L values
            hfd_values.append(np.nan)  # Append NaN or another placeholder to indicate failure
    
    return hfd_values

def calculate_zero_crossing_rate(signals):
    # Calculate sign changes across the signals array
    sign_changes = np.diff(np.sign(signals), axis=1)
    # Count zero crossings (where sign changes are non-zero)
    zero_crossings = np.count_nonzero(sign_changes, axis=1)
    # Normalize by the length of each signal segment analyzed
    zero_crossing_rates = zero_crossings / (signals.shape[1] - 1)
    return zero_crossing_rates
    
def perform_empirical_mode_decomposition(signals):
    # Initialize EMD
    emd = EMD()
    
    # Initialize an empty list to store IMFs for each signal
    imfs_all_signals = []
    
    # Loop through each signal
    for signal in signals:
        # Perform EMD decomposition for the current signal
        imfs = emd(signal)
        
        # Append the IMFs of the current signal to the list
        imfs_all_signals.append(imfs)
    
    # Convert the list of IMFs into a NumPy array
    imfs_all_signals = np.array(imfs_all_signals)
    
    return imfs

def time_warping_factor(signals): 
    # Calculate the average signal (mean across all signals for each time point)
    average_signal = np.mean(np.array(signals), axis=0)
    warping_factors = []
    for signal in signals:
        # Ensure that the signals are 1-D
        signal = np.atleast_1d(signal)
        average_signal = np.atleast_1d(average_signal)
        
        print("Signal shape:", signal.shape)
        print("Average signal shape:", average_signal.shape)
        distance, _ = fastdtw(np.squeeze(signal), np.squeeze(average_signal), dist=euclidean)
        

        
        # Calculate the DTW distance between the signal and the average signal
        distance, _ = fastdtw(np.squeeze(signal), np.squeeze(average_signal), dist=euclidean)

        # Append the distance as the warping factor for this signal
        warping_factors.append(distance)
    return warping_factors


def evolution_rate(signals): 
    rates = np.zeros(signals.shape[0])
    for i, signal in enumerate(signals):
        # Compute the analytic signal
        analytic_signal = hilbert(signal)
        # Calculate the envelope
        envelope = np.abs(analytic_signal)
        # Calculate the derivative of the envelope
        derivative = np.diff(envelope)
        # Compute the evolution rate as the average absolute derivative of the envelope
        rates[i] = np.mean(np.abs(derivative))
    return rates

def analyze_signals(buffer):
    signals = buffer.T  # Assuming signals are organized as channels x samples in the buffer
    
    peak_heights = detect_peak_heights(signals)
    peak_counts = detect_peaks(signals)
    variance, std_dev = calculate_variance_std_dev(signals)
    rms = calculate_rms(signals)
    band_features = freq_bands(signals, FS)
    spectral_entropy_dict = calculate_spectral_entropy(signals, list(range(NUM_CHANNELS)), FS)
    centroids = spectral_centroids(signals, FS)
    spectral_edge_densities = spectral_edge_density(signals, FS, 95)
    #plv = phase_locking_values(signals) 
    hfd_values = calculate_higuchi_fractal_dimension(signals, k_max=10)
    zero_crossing_rate = calculate_zero_crossing_rate(signals)
    #imfs = perform_empirical_mode_decomposition(signals) 
    #warping_factors = time_warping_factor(signals)
    rates = evolution_rate(signals)

    # Convert float32 values to Python floats in the results dictionary
    results = {
        'peak_heights': [float(val) for val in peak_heights],
        'peak_counts': [float(val) for val in peak_counts],
        'variance': [float(val) for val in variance],
        'std_dev': [float(val) for val in std_dev],
        'rms': [float(val) for val in rms],
        'band_features': [[float(val) for val in band] for band in band_features],
        'spectral_entropy': spectral_entropy_dict,
        'centroids': [float(val) for val in centroids],
        'spectral_edge_densities': [float(val) for val in spectral_edge_densities],
        #'phase_synchronization': plv.tolist(),
        'higuchi_fractal_dimension': [float(val) for val in hfd_values],
        'zero_crossing_rate': [float(val) for val in zero_crossing_rate],
        #'empirical_mode_decomposition': [imf.tolist() for imf in imfs],
        #'time_warping_factor': warping_factors.tolist(),
        'evolution_rate': [float(val) for val in rates],
    }
    return results


def main():
    context = zmq.Context()
    sub_socket = context.socket(zmq.SUB)
    sub_socket.connect("tcp://localhost:5444")
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, '')

    pub_socket = context.socket(zmq.PUB)
    pub_socket.bind("tcp://*:5445")
    
    buffer = np.zeros((NUM_CHANNELS, BUFFER_SIZE), dtype=np.float32)

    while True:
        neural_data = receive_neural_data(sub_socket)
        # Inside main(), after receiving and checking neural_data
        if neural_data is not None and neural_data.size > 0:
            scaled_data = scale_data(neural_data)  # Scale the received data
            buffer = buffer_data(scaled_data, buffer)  # Buffer the scaled data
        
            # Once the buffer is ready for analysis
            if np.all(buffer != 0):  # Assuming buffer is filled to a point where analysis is meaningful
                analysis_results = analyze_signals(buffer)  # Analyze the buffered signals
                serialized_results = json.dumps(analysis_results)  # Serialize analysis results
                pub_socket.send_string(serialized_results)  # Send the results

        else:
            print("No neural data received or neural_data is empty.")

if __name__ == "__main__":
    main()
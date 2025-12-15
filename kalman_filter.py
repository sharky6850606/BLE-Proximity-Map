"""Kalman Filter for RSSI smoothing (Option C: more stable distance)."""

class KalmanFilter:
    def __init__(
        self,
        process_variance: float = 0.3,
        measurement_variance: float = 9.0,
        max_step: float = 3.0,
    ):
        """Initialize a simple 1-D Kalman filter for RSSI.

        Args:
            process_variance: How much we allow the underlying true RSSI to move between
                samples. Smaller = smoother, slower to react.
            measurement_variance: How noisy we think the measurements are.
                Larger = smoother, trusts history more than new readings.
            max_step: Maximum allowed change (in dB) per update step to prevent
                large jumps from a single bad packet.
        """
        self.process_variance = float(process_variance)
        self.measurement_variance = float(measurement_variance)
        self.max_step = float(max_step)

        self.estimated = None
        self.covariance = 1.0

    def update(self, measurement: float | None) -> float | None:
        """Update filter with a new RSSI measurement and return smoothed RSSI.

        If `measurement` is None, returns the last estimate unchanged.
        """
        if measurement is None:
            return self.estimated

        m = float(measurement)

        # First sample: just take it as is.
        if self.estimated is None:
            self.estimated = m
            self.covariance = 1.0
            return self.estimated

        # --- Prediction ---
        self.covariance += self.process_variance

        # --- Update ---
        K = self.covariance / (self.covariance + self.measurement_variance)
        proposed = self.estimated + K * (m - self.estimated)

        # Hard limit on how far we can move in a single step to avoid big jumps
        delta = proposed - self.estimated
        if delta > self.max_step:
            proposed = self.estimated + self.max_step
        elif delta < -self.max_step:
            proposed = self.estimated - self.max_step

        self.estimated = proposed
        self.covariance = (1.0 - K) * self.covariance

        return self.estimated

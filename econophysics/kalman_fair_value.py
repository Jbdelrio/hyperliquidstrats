"""
kalman_fair_value.py — Kalman filter for microstructure-cleaned fair value.

State: x = [fair_value, drift]
Obs:   z = [mid_price, vwap_30s]

No look-ahead: pure filter (not smoother).
"""
import numpy as np
from typing import Optional, Tuple


class KalmanFairValue:
    """
    2D Kalman filter: tracks fair_value and its short-term drift.

    Transition:
        fv_{t+1}    = fv_t + drift_t  + noise
        drift_{t+1} = drift_t          + noise  (near-random walk)

    Observation (2 obs):
        mid_price  = fv_t + noise_mid
        vwap_30s   = fv_t + noise_vwap (noisier)
    """

    def __init__(self,
                 process_noise: float = 1e-7,
                 obs_noise_mid: float = 1e-5,
                 obs_noise_vwap: float = 5e-5,
                 initial_value: float = 100.0):

        self.x = np.array([initial_value, 0.0])
        self.P = np.eye(2) * 1.0

        self.F = np.array([[1.0, 1.0],
                           [0.0, 1.0]])

        self.H = np.array([[1.0, 0.0],
                           [1.0, 0.0]])

        self.Q = process_noise * np.array([[0.25, 0.5],
                                            [0.50, 1.0]])

        self.R = np.array([[obs_noise_mid,  0.0],
                           [0.0, obs_noise_vwap]])

        self._initialized = False

    def update(self, mid_price: float,
               vwap_30s: Optional[float] = None) -> Tuple[float, float]:
        """
        Ingest one observation. Returns (fair_value, drift).
        drift is per-tick; multiply by tick-rate to get per-second.
        """
        if not self._initialized:
            self.x = np.array([mid_price, 0.0])
            self._initialized = True
            return mid_price, 0.0

        vwap = vwap_30s if vwap_30s is not None else mid_price

        # Predict
        x_pred = self.F @ self.x
        P_pred = self.F @ self.P @ self.F.T + self.Q

        # Update
        z = np.array([mid_price, vwap])
        innov = z - self.H @ x_pred
        S = self.H @ P_pred @ self.H.T + self.R

        try:
            K = P_pred @ self.H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:
            self.x = x_pred
            self.P = P_pred
            return float(self.x[0]), float(self.x[1])

        self.x = x_pred + K @ innov
        self.P = (np.eye(2) - K @ self.H) @ P_pred

        return float(self.x[0]), float(self.x[1])

    def get_fair_value(self) -> float:
        return float(self.x[0])

    def get_drift(self) -> float:
        """Drift per tick (positive = upward pressure)."""
        return float(self.x[1])

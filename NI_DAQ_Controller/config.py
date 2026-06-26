"""
Configuration management module for NI DAQ Controller.

This module handles application configuration, including default settings,
user preferences, and hardware-specific configurations. It uses YAML for
configuration file storage and provides thread-safe access to settings.

Typical usage:
    config = AppConfig()
    config.load()
    sample_rate = config.get('acquisition.sample_rate', 1000)
    config.set('acquisition.sample_rate', 5000)
    config.save()
"""

import os
import yaml
import threading
from typing import Any, Dict, Optional
from pathlib import Path


class AppConfig:
    """
    Application configuration manager.

    Handles loading, saving, and accessing configuration values with
    thread-safe operations and default value fallbacks.

    Attributes:
        config_dir: Directory path for configuration files
        config_file: Full path to the YAML configuration file
        _config: Internal configuration dictionary
        _lock: Thread lock for safe concurrent access
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        'application': {
            'title': 'NI DAQ Controller',
            'theme': 'dark-blue',
            'window_size': (1400, 900),
            'min_window_size': (1024, 600),
        },
        'acquisition': {
            'default_sample_rate': 1000.0,
            'max_sample_rate': 1000000.0,
            'default_samples_per_channel': 100,
            'timeout_seconds': 10.0,
        },
        'monitoring': {
            'default_refresh_rate_ms': 100,
            'max_refresh_rate_ms': 1000,
            'min_refresh_rate_ms': 10,
            'buffer_size': 1000,
        },
        'logging': {
            'level': 'INFO',
            'max_file_size_mb': 10,
            'backup_count': 5,
            'log_to_console': True,
            'log_to_file': True,
        },
        'output': {
            'default_voltage_range': [-10, 10],
            'max_voltage_range': [-10, 10],
            'default_frequency': 60.0,
            'max_frequency': 10000.0,
        },
        'csv_export': {
            'delimiter': ',',
            'include_timestamp': True,
            'date_format': '%Y-%m-%d %H:%M:%S.%f',
        },
    }

    def __init__(self, config_dir: Optional[str] = None) -> None:
        """
        Initialize the configuration manager.

        Args:
            config_dir: Optional custom configuration directory.
                        Defaults to '~/.ni_daq_controller/'
        """
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path.home() / '.ni_daq_controller'

        self.config_file = self.config_dir / 'config.yaml'
        self._config: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def load(self) -> None:
        """
        Load configuration from file.

        If the configuration file does not exist, default settings are used.
        Missing keys in an existing file are filled with defaults.
        """
        with self._lock:
            self._config = self._deep_copy(self.DEFAULT_CONFIG)

            if self.config_file.exists():
                try:
                    with open(self.config_file, 'r') as f:
                        loaded_config = yaml.safe_load(f)
                    if loaded_config:
                        self._merge_config(self._config, loaded_config)
                except (yaml.YAMLError, IOError) as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Failed to load config file: {e}. Using defaults."
                    )
            else:
                self._ensure_config_dir()
                self.save()

            self._loaded = True

    def save(self) -> None:
        """
        Save current configuration to file.

        Creates the configuration directory if it does not exist.
        """
        with self._lock:
            self._ensure_config_dir()
            try:
                with open(self.config_file, 'w') as f:
                    yaml.dump(
                        self._config,
                        f,
                        default_flow_style=False,
                        indent=2,
                        sort_keys=False
                    )
            except IOError as e:
                import logging
                logging.getLogger(__name__).error(
                    f"Failed to save config file: {e}"
                )

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot-notation key.

        Args:
            key: Dot-notation configuration key (e.g., 'acquisition.sample_rate')
            default: Default value if key is not found

        Returns:
            Configuration value or default if not found
        """
        with self._lock:
            if not self._loaded:
                self.load()

            keys = key.split('.')
            value = self._config

            try:
                for k in keys:
                    value = value[k]
                return value
            except (KeyError, TypeError):
                return default

    def set(self, key: str, value: Any) -> None:
        """
        Set a configuration value using dot-notation key.

        Creates intermediate dictionaries as needed.

        Args:
            key: Dot-notation configuration key
            value: Value to set
        """
        with self._lock:
            if not self._loaded:
                self.load()

            keys = key.split('.')
            config = self._config

            for k in keys[:-1]:
                if k not in config:
                    config[k] = {}
                config = config[k]

            config[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        """
        Get a deep copy of the entire configuration.

        Returns:
            Complete configuration dictionary
        """
        with self._lock:
            return self._deep_copy(self._config)

    def reset_to_defaults(self) -> None:
        """
        Reset all configuration values to defaults.
        """
        with self._lock:
            self._config = self._deep_copy(self.DEFAULT_CONFIG)
            self.save()

    def _ensure_config_dir(self) -> None:
        """
        Create the configuration directory if it doesn't exist.
        """
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            import logging
            logging.getLogger(__name__).error(
                f"Failed to create config directory: {e}"
            )

    def _merge_config(self, base: Dict, override: Dict) -> None:
        """
        Recursively merge override config into base config.

        Args:
            base: Base configuration dictionary (modified in place)
            override: Override configuration dictionary
        """
        for key, value in override.items():
            if (key in base and isinstance(base[key], dict)
                    and isinstance(value, dict)):
                self._merge_config(base[key], value)
            else:
                base[key] = value

    def _deep_copy(self, data: Any) -> Any:
        """
        Create a deep copy of configuration data.

        Args:
            data: Data to copy

        Returns:
            Deep copy of the input data
        """
        if isinstance(data, dict):
            return {k: self._deep_copy(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._deep_copy(v) for v in data]
        return data


# Global configuration instance for application-wide use
global_config = AppConfig()
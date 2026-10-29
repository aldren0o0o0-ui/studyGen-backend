import pytest
from config import Settings

def test_migration_configuration_defaults():
    # Instantiate without env vars to check strict defaults
    settings = Settings(_env_file=None)
    
    assert settings.auto_migrate is False
    assert settings.enable_legacy_history_migration is False

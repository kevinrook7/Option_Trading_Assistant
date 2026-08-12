import os
import yaml
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def load_config(config_path="config.yaml"):
    """Loads the YAML configuration file."""
    with open(config_path, 'r') as file:
        config = yaml.safe_load(file)
    return config

def get_env_var(key):
    """Retrieves an environment variable by key."""
    return os.getenv(key)
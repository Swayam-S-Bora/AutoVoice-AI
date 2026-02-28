import logging
import sys
from datetime import datetime
from typing import Optional
import os

# Create logs directory if it doesn't exist
logs_dir = 'logs'
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)
    print(f"Created {logs_dir} directory")  # This will show in terminal

# Configure logging
def setup_logger(name: str, log_file: Optional[str] = None, level=logging.INFO):
    """Setup a logger with file and console handlers"""
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    simple_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Console handler (prints to terminal)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(simple_formatter)
    
    # File handler (saves to file)
    if log_file:
        file_handler = logging.FileHandler(f'logs/{log_file}')
        file_handler.setLevel(level)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    
    logger.addHandler(console_handler)
    return logger

# Create main application logger
app_logger = setup_logger('autovoice', 'app.log')

# Create a special logger for calls
call_logger = setup_logger('calls', 'calls.log')
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(name: str) -> logging.Logger:
    """
    Configure and return a logger instance with both console and file handlers.
    
    Args:
        name (str): Name of the logger instance
        
    Returns:
        logging.Logger: Configured logger instance
    """
    # Create logger instance
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # getLogger(name) returns the SAME cached logger object on every call for
    # a given name (e.g. re-imports under uvicorn --reload) - skip re-adding
    # handlers if this logger is already configured, or every message would
    # print once per past call to setup_logger(name).
    if logger.handlers:
        return logger

    # Don't propagate to the root logger: third-party libraries (e.g. absl-py,
    # pulled in by google-generativeai) attach their own handler to the root
    # logger, so a propagated record gets printed a second time in their
    # format on top of ours.
    logger.propagate = False

    # Create handlers
    console_handler = logging.StreamHandler()
    file_handler = RotatingFileHandler(
        'server.log',
        maxBytes=1024*1024,
        backupCount=5
    )

    # Create formatter and add it to handlers
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(log_format)
    file_handler.setFormatter(log_format)

    # Add handlers to the logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
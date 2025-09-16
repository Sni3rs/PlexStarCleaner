# Use an official Python runtime as a parent image
FROM python:3.11-slim-bullseye

# Set environment variables
# Set the timezone to prevent scheduling issues
ENV TZ=Etc/UTC
# Prevents Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY main.py .

# Command to run the application
CMD ["python", "main.py"]


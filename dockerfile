# Use a lightweight Python base image
FROM python:slim

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

RUN apt update

RUN apt install curl -y

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy template file
COPY config.template.yml .

# Copy application code
COPY app.py .

# Expose port
EXPOSE ${PORT:-8000}

# Command to run the application
CMD ["python", "app.py"]

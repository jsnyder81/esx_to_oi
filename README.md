# Ekahau to OpenIntent Converter

This tool converts Ekahau project data (extracted .esx format) into an OpenIntent schema compatible JSON document. It also processes floorplan images, placing access points on them based on the Ekahau data.

## Installation

1.  Clone the repository.
2.  Install the required Python packages:

    ```bash
    pip install -r requirements.txt
    ```

## Prerequisites

This tool requires the OpenIntent Wi-Fi schema (`oi-wifi.schema.json`). You can download the latest release from the OpenIntent Models repository.

## Configuration

The script requires the OpenIntent Wi-Fi schema (`oi-wifi.schema.json`). You can specify the location of this schema in three ways (in order of precedence):

1.  **Command Line Argument**: `--schema /path/to/schema.json`
2.  **Environment Variable**: `OI_SCHEMA_PATH`
3.  **Config File**: Create a `config.yaml` in the script directory with the following content:

    ```yaml
    ---
    schema_path: "/path/to/oi-wifi.schema.json"
    ```

## Usage

Run the script:

```bash
python3 esx_to_oi.py <path_to_esx_file> -o <output_directory>
```
---
name: weather
description: Retrieves weather forecast for a given city. Invoke when the user asks for weather information or forecast.
---

# Weather Forecast Skill

This skill allows you to check the weather forecast for any city using the `forecast.py` script.

## Prerequisites

- **Python 3**: This skill requires Python 3 to be installed and available in your environment.
- **Standard Library Only**: No external Python packages are needed (it uses `urllib`, `json`, `ssl` which are built-in).

## Usage

To use this skill, execute the `forecast.py` script located in this skill's directory.

```bash
python3 <path-to-skill>/forecast.py <city_name>
```

- `<city_name>`: The name of the city you want to check the weather for. It can be in English (e.g., "London") or Chinese (e.g., "深圳").
- If no city name is provided, it defaults to "深圳".

## Examples

- Check weather for Shenzhen (default):
  ```bash
  python3 <path-to-skill>/forecast.py
  ```

- Check weather for Beijing:
  ```bash
  python3 <path-to-skill>/forecast.py Beijing
  ```

- Check weather for London:
  ```bash
  python3 <path-to-skill>/forecast.py London
  ```

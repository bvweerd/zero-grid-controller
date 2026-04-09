---
paths:
  - "tests/**/*"
---

# Testing

- Framework: `pytest-homeassistant-custom-component`
- `asyncio_mode = auto` (all test coroutines run automatically)
- Use `hass` fixture for the `HomeAssistant` instance
- Use `MockConfigEntry` from `pytest_homeassistant_custom_component.common`
- Add an `auto_enable_custom_integrations` autouse fixture per test module:
  ```python
  @pytest.fixture(autouse=True)
  def auto_enable_custom_integrations(enable_custom_integrations):
      return
  ```
- Mock external HA state with `hass.states.async_set()`
- Run tests: `python -m pytest tests/ -v`
- Run single file: `python -m pytest tests/test_coordinator.py -v`

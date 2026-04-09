---
name: validate-hacs
description: Validate the HACS integration for compliance
---

# Validate HACS

1. Check `manifest.json`: domain=`zero_grid_controller`, name, version, codeowners=`[@bvweerd]`, iot_class, quality_scale
2. Check `hacs.json`: name, render_readme, filename
3. Check file structure: `custom_components/zero_grid_controller/__init__.py` present
4. Check for `README.md`
5. Verify strings/translations: `strings.json` and `translations/en.json` are in sync
6. Run hassfest if available: `python -m script.hassfest`
7. Report issues and suggest fixes

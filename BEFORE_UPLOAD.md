# Before uploading to GitHub

This file is only a checklist – delete it after the upload.

## 1. Replace the placeholders

`YOUR-GITHUB-NAME` appears in a few files. Quickest way, from the repo folder:

```bash
grep -rl 'YOUR-GITHUB-NAME' . | xargs sed -i 's/YOUR-GITHUB-NAME/yourname/g'
```

Affected:

| File | What |
|---|---|
| `custom_components/dimplex_nwpm/manifest.json` | `codeowners`, `documentation`, `issue_tracker` |
| `README.md` | badge links |

Also replace `YOUR-NAME` in `LICENSE` with your name.

If your repository is not called `ha-dimplex-nwpm`, adjust that part of the URLs too.

## 2. Create the repository

HACS has three requirements that don't come from files:

- It must be **public**.
- It needs a **description** (the "About" field). HACS displays this.
  Suggestion: *Home Assistant integration for Dimplex heat pumps via NWPM Modbus TCP*
- It needs **topics**. Suggestion: `home-assistant`, `hacs`, `homeassistant`,
  `custom-component`, `modbus`, `dimplex`, `heat-pump`

## 3. Upload

```bash
cd <this folder>
git init
git add .
git commit -m "Initial release 0.6.0"
git branch -M main
git remote add origin https://github.com/yourname/ha-dimplex-nwpm.git
git push -u origin main
```

## 4. Create a release

HACS works without releases but then only offers the default branch. With releases,
users get version selection and update notifications.

On GitHub: *Releases → Create a new release → tag `v0.6.0`*, and paste the text from
`CHANGELOG.md`. It must be a **full release**, not just a tag.

For every future version, bump `version` in `manifest.json` and create a matching
release – HACS compares exactly those two.

## 5. Check the CI

After pushing, two checks run under *Actions*: **hassfest** (Home Assistant) and
**HACS**. Both should be green. The brands check is deliberately disabled in the
workflow because it requires an entry in the `home-assistant/brands` repository – that
is only needed if you later submit the integration as a HACS default repository.

## 6. Switch your own installation over (optional)

If you have been copying the integration manually, you can manage it through HACS
afterwards: HACS → menu (⋮) → *Custom repositories* → your repo URL, type
*Integration*. Delete the manually copied folder first so there aren't two copies.

**Note on entity IDs:** version 0.6.0 renames the device to "Dimplex Heat Pump". Home
Assistant assigns entity IDs once, so your existing entities keep their German IDs
(`sensor.dimplex_warmepumpe_aussentemperatur` and so on) and your current dashboard
keeps working. The shipped `dashboard_card.yaml` uses the new English IDs, so it will
not match your installation unless you rename the entities. Two options:

- Keep your IDs and edit the card to match, or
- open the device page, rename the device, and tick "also rename entity IDs" – then
  everything lines up with the shipped card.

## Note on the icons

`brands/icon.png` and `brands/logo.png` are machine-generated placeholders. Home
Assistant does **not** pick them up automatically – that would require an entry in the
`home-assistant/brands` repository. Feel free to replace or delete them.

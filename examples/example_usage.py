from scripts.titech_lms import (
    MoodleClient,
    MoodleCredentials,
    ScienceTokyoPortalAccount,
    ScienceTokyoPortalClient,
    load_config,
)

config = load_config("./config.json")
account = ScienceTokyoPortalAccount.from_config(config=config)
moodle_config = MoodleCredentials.from_config(config=config)

portal = ScienceTokyoPortalClient(lms_base_url=moodle_config.base_url)
portal.login(account)
portal.get_lms_dashboard()

# Get a Moodle mobile WebService token through the Extic-authenticated session.
ws_token = portal.get_lms_token()
print("Moodle token:", ws_token)

# Use the Moodle REST wrapper.
moodle = MoodleClient(moodle_config.base_url, ws_token)
print(moodle.get_site_info())

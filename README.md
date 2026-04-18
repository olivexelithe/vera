# Vera Discord Verification Bot

Vera is a Discord bot for NOVA verification. She posts a verification panel, creates a private verification thread for each applicant, asks for a recent game profile screenshot, and lets admin/logistics staff verify or reject the applicant with buttons.

## What Vera Does

- Automatically gives new members the waiting room role.
- Posts a verification panel that says: "Welcome to NOVA! Please click the below button to verify."
- Opens a private verification thread when a member clicks **Verify**.
- Adds the applicant, current admin role members, and current logistics role members to the private thread.
- Asks the applicant to send a recent screenshot of their game profile.
- Shows staff-only buttons:
  - **Verify PVP**
  - **Verify N0VA**
  - **Verify Guest Pass**
  - **Reject**
- Gives the selected PVP, N0VA, or guest pass role and removes the waiting room role.
- Leaves rejected members in the waiting room role.
- States which staff member verified or rejected the applicant.
- Sends verified members to the roles channel to select their roles.

## Discord Setup

1. Create a Discord application at <https://discord.com/developers/applications>.
2. Add a bot user.
3. Enable these privileged gateway intents for the bot:
   - Server Members Intent
4. Invite the bot to your server with these permissions:
   - Manage Roles
   - Manage Threads
   - Create Private Threads
   - Send Messages
   - Send Messages in Threads
   - Embed Links
   - Use Slash Commands
   - Read Message History
5. Make sure Vera's highest role is above the waiting room, PVP, N0VA, and guest pass roles.
6. In the verification channel, make sure Vera can create private threads.

Private thread access note: Discord does not let bots grant a role direct access to one private thread. Vera adds the applicant plus the current members of your admin and logistics roles to each thread. The verification buttons are also locked so only those teams, or administrators, can use them.

## Environment Variables

Copy `.env.example` to `.env` for local testing, or add these variables in Railway:

```env
DISCORD_TOKEN=replace_with_your_bot_token
GUILD_ID=replace_with_your_server_id
WAITING_ROOM_ROLE_ID=replace_with_waiting_room_role_id
PVP_ROLE_ID=replace_with_pvp_role_id
NOVA_ROLE_ID=replace_with_n0va_role_id
GUEST_PASS_ROLE_ID=replace_with_guest_pass_role_id
ADMIN_ROLE_ID=replace_with_admin_team_role_id
LOGISTICS_ROLE_ID=replace_with_logistics_team_role_id
ROLES_CHANNEL_ID=replace_with_roles_channel_id
```

To copy an ID in Discord, turn on **User Settings > Advanced > Developer Mode**, then right-click the server, role, or channel and choose **Copy ID**.

## Local Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Posting Vera's Verification Panel

Once Vera is online:

1. Go to your verification channel.
2. Run `/vera`.
3. Vera will post the welcome box and the **Verify** button.

The button is persistent, so it will keep working after the bot restarts.

## Railway Deployment

1. Push this folder to a GitHub repository.
2. Create a new Railway project from that GitHub repo.
3. Add the environment variables listed above.
4. Railway should detect the Python app and use the `Procfile`:

```Procfile
worker: python bot.py
```

5. Deploy.

Vera is a worker bot, not a web app, so she does not need a public port.

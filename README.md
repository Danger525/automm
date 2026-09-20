# AutoMM — Discord Ticket-Style Escrow Bot

A secure Discord middleman and escrow deal-room bot built with `discord.py`.

## Features
- 🎫 **Private Deal Rooms**: Isolated ticket channel creation per buyer.
- 🤝 **Buyer & Seller Collaboration**: Intuitive UI modals and buttons to add the counterparty and negotiate terms.
- 🔒 **Mutual Agreement Lock**: Terms (amount, currency, network) are locked once both parties sign off.
- ⚖️ **Dispute Management**: In-ticket dispute handling for middleman staff intervention.
- 📁 **Transcripts & Archiving**: Exports full chat logs to a designated staff transcript channel before deletion.

## Setup & Installation

1. **Clone the repository**:
   ```bash
   git clone <your-repo-url>
   cd discord-automm
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Fill in the variables in `.env`:
   - `DISCORD_TOKEN`: Your bot token from [Discord Developer Portal](https://discord.com/developers/applications).
   - `MIDDLEMAN_ROLE_ID`: ID of the escrow/middleman role with staff permissions.
   - `TICKET_CATEGORY_ID`: Category ID where deal channels will be spawned.
   - `TRANSCRIPT_CHANNEL_ID`: Channel ID where text transcripts will be saved when tickets are closed.

4. **Bot Privileged Intents**:
   In the Discord Developer Portal under the **Bot** tab, ensure the following Privileged Gateway Intents are enabled:
   - **Server Members Intent**
   - **Message Content Intent**

5. **Run the Bot**:
   ```bash
   python bot.py
   ```

6. **Deploy the Panel**:
   In your Discord server, run the setup command as an administrator:
   ```
   $setup_panel
   ```

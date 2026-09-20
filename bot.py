import os
import io
import json
import logging
import asyncio
from typing import Optional, Dict, Any

import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from dotenv import load_dotenv

from client import AutoMMClient, AutoMMApiError

load_dotenv()

# ============================================================
# AutoMM Ticket-Style Discord Bot
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("AutoMMTicketBot")

BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
MIDDLEMAN_ROLE_ID = int(os.environ.get("MIDDLEMAN_ROLE_ID", "0"))
TICKET_CATEGORY_ID = int(os.environ.get("TICKET_CATEGORY_ID", "0"))
TRANSCRIPT_CHANNEL_ID = int(os.environ.get("TRANSCRIPT_CHANNEL_ID", "0"))
AUTOMM_API_URL = os.environ.get("AUTOMM_API_URL", "http://127.0.0.1:8000/api/v1")
BACKEND_SECRET = os.environ.get("BACKEND_SECRET", "automm-dev-secret-key-change-in-production")

BRAND_NAME = "AutoMM Secure Escrow"
DATA_FILE = "tickets.json"

automm_client = AutoMMClient(base_url=AUTOMM_API_URL)

# ------------------------------------------------------------
# Persistent Ticket State Storage
# ------------------------------------------------------------

def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data: Dict[str, Any]):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


tickets = load_data()


def get_ticket(channel_id: int) -> Optional[Dict[str, Any]]:
    return tickets.get(str(channel_id))


def update_ticket(channel_id: int, **kwargs):
    key = str(channel_id)
    if key not in tickets:
        tickets[key] = {}
    tickets[key].update(kwargs)
    save_data(tickets)


def delete_ticket(channel_id: int):
    tickets.pop(str(channel_id), None)
    save_data(tickets)


# ------------------------------------------------------------
# Permission & Role Helpers
# ------------------------------------------------------------

def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id == MIDDLEMAN_ROLE_ID for role in member.roles)


def is_valid_evm_address(address: str) -> bool:
    if not address or not isinstance(address, str):
        return False
    clean = address.strip()
    return clean.startswith("0x") and len(clean) == 42


# ------------------------------------------------------------
# Deal Room Embed Builder
# ------------------------------------------------------------

def build_deal_embed(channel: discord.TextChannel) -> discord.Embed:
    data = get_ticket(channel.id) or {}

    buyer_id = data.get("buyer_id")
    seller_id = data.get("seller_id")
    deal_id = data.get("deal_id", "Unknown")

    buyer = channel.guild.get_member(buyer_id) if buyer_id else None
    seller = channel.guild.get_member(seller_id) if seller_id else None

    amount = data.get("amount") or "Not set"
    token = data.get("token") or "ETH"
    network = data.get("network") or "SEPOLIA"
    status_str = data.get("status", "WAITING_FOR_SELLER")

    deposit_addr = data.get("deposit_address")
    confirmations = data.get("confirmations", 0)
    required_confs = data.get("required_confirmations", 2)
    latest_tx = data.get("latest_tx_hash")

    # Status formatting
    if status_str == "WAITING_FOR_SELLER":
        status_display = "🔵 Waiting for seller to be added"
        color = 0x5865F2
    elif status_str == "WAITING_FOR_TERMS":
        status_display = "🔵 Waiting for deal terms (amount/token) to be set"
        color = 0x5865F2
    elif status_str == "WAITING_FOR_AGREEMENT":
        b_agree = "✅ Agreed" if data.get("buyer_agreed") else "❌ Not agreed"
        s_agree = "✅ Agreed" if data.get("seller_agreed") else "❌ Not agreed"
        status_display = f"🟡 Waiting for agreement\n• Buyer: {b_agree}\n• Seller: {s_agree}"
        color = 0xFEE75C
    elif status_str in ("AGREED", "ESCROW_CREATING"):
        status_display = "🟢 Terms locked! Creating on-chain escrow wallet..."
        color = 0x57F287
    elif status_str == "WAITING_FOR_PAYMENT":
        status_display = "🟡 Escrow Ready — Waiting for Buyer Payment"
        color = 0xFEE75C
    elif status_str == "PAYMENT_DETECTED":
        status_display = f"⏳ Payment Detected — Confirming ({confirmations}/{required_confs})"
        color = 0xEB459E
    elif status_str == "FUNDED":
        status_display = f"🟢 **FUNDED** ({confirmations}/{required_confs} Confirmations)\nSeller can now deliver item/service."
        color = 0x57F287
    elif status_str == "DELIVERED":
        status_display = "📦 **DELIVERED** — Seller marked delivery complete. Buyer must release funds."
        color = 0x57F287
    elif status_str == "RELEASE_PENDING":
        status_display = "⏳ Payout Processing — Broadcasting transaction to testnet..."
        color = 0xFEE75C
    elif status_str == "COMPLETED":
        status_display = "✅ **DEAL COMPLETED** — Funds released to seller."
        color = 0x57F287
    elif status_str == "DISPUTED":
        status_display = "⚠️ **DISPUTED** — Funds held in escrow. Middleman review required."
        color = 0xED4245
    elif status_str == "REFUNDED":
        status_display = "↩️ **REFUNDED** — Escrowed funds returned to buyer."
        color = 0x95A5A6
    else:
        status_display = f"🔵 Status: {status_str}"
        color = 0x5865F2

    title_header = "ESCROW READY" if deposit_addr and status_str in ("WAITING_FOR_PAYMENT", "PAYMENT_DETECTED") else "AutoMM DEAL ROOM"
    if status_str == "COMPLETED":
        title_header = "DEAL COMPLETED"

    embed = discord.Embed(
        title=f"╔══════════════════════════╗\n  {BRAND_NAME} — {title_header}\n╚══════════════════════════╝",
        color=color
    )

    embed.add_field(
        name="Buyer",
        value=buyer.mention if buyer else (f"<@{buyer_id}>" if buyer_id else "Unknown"),
        inline=True
    )

    embed.add_field(
        name="Seller",
        value=seller.mention if seller else (f"<@{seller_id}>" if seller_id else "Not added"),
        inline=True
    )

    embed.add_field(
        name="Deal ID",
        value=f"`{deal_id[:8]}...`" if len(deal_id) > 8 else f"`{deal_id}`",
        inline=True
    )

    embed.add_field(
        name="Deal Amount",
        value=f"`{amount} {token}`" if amount != "0" and amount != "Not set" else "`Not set`",
        inline=True
    )

    embed.add_field(
        name="Network",
        value=f"`{network}`",
        inline=True
    )

    embed.add_field(
        name="Status",
        value=status_display,
        inline=False
    )

    # If escrow address is generated, prominently display deposit instructions
    if deposit_addr:
        embed.add_field(
            name="📥 Escrow Deposit Address (Send Exact Amount Here)",
            value=f"```\n{deposit_addr}\n```\n⚠️ *Send exactly `{amount} {token}` on `{network}`. Never send real mainnet funds.*",
            inline=False
        )

    if latest_tx:
        embed.add_field(
            name="🔗 Blockchain Transaction",
            value=f"[`{latest_tx[:16]}...`](https://sepolia.etherscan.io/tx/{latest_tx})",
            inline=False
        )

    embed.set_footer(text="AutoMM Escrow • Testnet Only • Keys Encrypted at Rest")
    return embed


# ------------------------------------------------------------
# Modals
# ------------------------------------------------------------

class AddSellerModal(Modal, title="Add Seller"):
    seller = TextInput(
        label="Seller Discord ID or @mention",
        placeholder="Example: 123456789012345678",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        data = get_ticket(channel.id)
        if not data:
            await interaction.response.send_message("Ticket data was not found.", ephemeral=True)
            return

        if interaction.user.id != data["buyer_id"] and not is_staff(interaction.user):
            await interaction.response.send_message("Only the buyer or staff can add the seller.", ephemeral=True)
            return

        raw = self.seller.value.strip().replace("<@", "").replace("!", "").replace(">", "")
        try:
            seller_id = int(raw)
        except ValueError:
            await interaction.response.send_message("Please enter a valid Discord user ID or mention.", ephemeral=True)
            return

        if seller_id == data["buyer_id"]:
            await interaction.response.send_message("The buyer cannot be the seller.", ephemeral=True)
            return

        seller = interaction.guild.get_member(seller_id)
        if not seller:
            try:
                seller = await interaction.guild.fetch_member(seller_id)
            except Exception:
                seller = None

        if not seller:
            await interaction.response.send_message("User is not a member of this server.", ephemeral=True)
            return

        await interaction.response.defer()

        # Update backend
        deal_id = data.get("deal_id")
        if deal_id:
            try:
                await automm_client.add_seller(deal_id, str(seller.id))
            except AutoMMApiError as e:
                await interaction.followup.send(f"❌ Backend error: {e.message}", ephemeral=True)
                return

        # Give seller channel permissions
        await channel.set_permissions(
            seller,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True
        )

        update_ticket(
            channel.id,
            seller_id=seller.id,
            status="WAITING_FOR_TERMS" if data.get("amount") in (None, "0", "Not set") else "WAITING_FOR_AGREEMENT",
            seller_agreed=False
        )

        await channel.send(f"✅ {seller.mention} has been added as the seller.")
        await refresh_deal_panel(channel)


class SetDealModal(Modal, title="Set Escrow Deal Terms"):
    amount = TextInput(
        label="Amount (in ETH)",
        placeholder="Example: 0.001",
        required=True,
        max_length=30
    )

    token = TextInput(
        label="Token Symbol",
        default="ETH",
        required=True,
        max_length=20
    )

    network = TextInput(
        label="Network",
        default="SEPOLIA",
        required=True,
        max_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        data = get_ticket(channel.id)
        if not data:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return

        if interaction.user.id != data["buyer_id"] and not is_staff(interaction.user):
            await interaction.response.send_message("Only the buyer or staff can set terms.", ephemeral=True)
            return

        if data.get("terms_locked"):
            await interaction.response.send_message("Terms are locked and cannot be changed.", ephemeral=True)
            return

        amt_str = self.amount.value.strip().replace("$", "").replace("ETH", "").strip()
        try:
            val = float(amt_str)
            if val <= 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("Please enter a valid positive number for amount.", ephemeral=True)
            return

        tok_str = self.token.value.strip().upper()
        net_str = self.network.value.strip().upper()

        await interaction.response.defer()

        # Update backend terms
        deal_id = data.get("deal_id")
        if deal_id:
            try:
                await automm_client.set_terms(deal_id, amt_str, token=tok_str, network=net_str)
            except AutoMMApiError as e:
                await interaction.followup.send(f"❌ Backend error: {e.message}", ephemeral=True)
                return

        update_ticket(
            channel.id,
            amount=amt_str,
            token=tok_str,
            network=net_str,
            status="WAITING_FOR_AGREEMENT" if data.get("seller_id") else "WAITING_FOR_SELLER",
            buyer_agreed=False,
            seller_agreed=False
        )

        await channel.send(f"📝 Deal terms set: `{amt_str} {tok_str}` on `{net_str}`. Both parties must now agree.")
        await refresh_deal_panel(channel)


class BuyerAgreeModal(Modal, title="Buyer Agreement & Refund Address"):
    refund_address = TextInput(
        label="Your Sepolia Wallet Address (for refund)",
        placeholder="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        required=True,
        max_length=42,
        min_length=42
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        addr = self.refund_address.value.strip()
        if not is_valid_evm_address(addr):
            await interaction.response.send_message("❌ Invalid EVM wallet address. Must start with 0x and be 42 characters.", ephemeral=True)
            return

        await handle_party_agreement(interaction, channel, user_id=interaction.user.id, refund_address=addr)


class SellerAgreeModal(Modal, title="Seller Agreement & Payout Address"):
    payout_address = TextInput(
        label="Your Sepolia Wallet Address (for payout)",
        placeholder="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        required=True,
        max_length=42,
        min_length=42
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        addr = self.payout_address.value.strip()
        if not is_valid_evm_address(addr):
            await interaction.response.send_message("❌ Invalid EVM wallet address. Must start with 0x and be 42 characters.", ephemeral=True)
            return

        await handle_party_agreement(interaction, channel, user_id=interaction.user.id, payout_address=addr)


class DisputeModal(Modal, title="Open Escrow Dispute"):
    reason = TextInput(
        label="Reason for Dispute",
        placeholder="Describe the issue with the counterparty...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        data = get_ticket(channel.id)
        if not data:
            return

        deal_id = data.get("deal_id")
        await interaction.response.defer()

        try:
            await automm_client.open_dispute(deal_id, requester_id=str(interaction.user.id), reason=self.reason.value.strip())
            update_ticket(channel.id, status="DISPUTED")
            await channel.send(f"⚠️ {interaction.user.mention} opened a dispute:\n> {self.reason.value.strip()}\nAn authorized Middleman staff member must review this ticket.")
            await refresh_deal_panel(channel)
        except AutoMMApiError as e:
            await interaction.followup.send(f"❌ Could not open dispute: {e.message}", ephemeral=True)


# ------------------------------------------------------------
# Agreement & Escrow Creation Helper
# ------------------------------------------------------------

async def handle_party_agreement(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    user_id: int,
    refund_address: Optional[str] = None,
    payout_address: Optional[str] = None
):
    data = get_ticket(channel.id)
    if not data:
        await interaction.response.send_message("Ticket not found.", ephemeral=True)
        return

    deal_id = data.get("deal_id")
    await interaction.response.defer()

    try:
        # Call backend agree endpoint
        resp = await automm_client.agree(
            deal_id=deal_id,
            user_id=str(user_id),
            buyer_refund_address=refund_address,
            seller_payout_address=payout_address
        )

        update_ticket(
            channel.id,
            buyer_agreed=resp.get("buyer_agreed", False),
            seller_agreed=resp.get("seller_agreed", False),
            terms_locked=resp.get("terms_locked", False),
            status=resp.get("status")
        )

        await channel.send(f"✅ Agreement recorded for {interaction.user.mention}.")

        # Check if both parties have agreed and terms are locked!
        if resp.get("terms_locked"):
            await channel.send("🔒 **Both parties have agreed — terms locked!**\nCreating secure on-chain escrow wallet on Ethereum Sepolia...")

            # Automatically generate escrow wallet on backend
            escrow_resp = await automm_client.create_escrow(deal_id, actor_id=str(interaction.user.id))
            dep_addr = escrow_resp.get("deposit_address")

            update_ticket(
                channel.id,
                deposit_address=dep_addr,
                status=escrow_resp.get("status", "WAITING_FOR_PAYMENT")
            )

            await channel.send(
                f"🎉 **Escrow Wallet Ready!**\n"
                f"Deposit Address: `{dep_addr}`\n"
                f"Buyer, please send exactly `{data.get('amount')} {data.get('token', 'ETH')}` on `{data.get('network', 'SEPOLIA')}`.\n"
                f"The backend monitor will automatically detect the transaction and confirm it on-chain."
            )

        await refresh_deal_panel(channel)

    except AutoMMApiError as e:
        await interaction.followup.send(f"❌ Agreement error: {e.message}", ephemeral=True)


# ------------------------------------------------------------
# Interactive Deal Room View
# ------------------------------------------------------------

class DealRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Add Seller", style=discord.ButtonStyle.primary, custom_id="automm_add_seller")
    async def add_seller(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddSellerModal())

    @discord.ui.button(label="Set Deal", style=discord.ButtonStyle.secondary, custom_id="automm_set_deal")
    async def set_deal(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SetDealModal())

    @discord.ui.button(label="Agree", style=discord.ButtonStyle.success, custom_id="automm_agree")
    async def agree(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        data = get_ticket(channel.id)
        if not data:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return

        if not data.get("seller_id"):
            await interaction.response.send_message("A seller must be added before agreeing.", ephemeral=True)
            return

        if not data.get("amount") or data.get("amount") in ("0", "Not set"):
            await interaction.response.send_message("Deal terms (amount) must be set before agreeing.", ephemeral=True)
            return

        if interaction.user.id == data["buyer_id"]:
            # Buyer agrees -> modal for refund address
            await interaction.response.send_modal(BuyerAgreeModal())
        elif interaction.user.id == data["seller_id"]:
            # Seller agrees -> modal for payout address
            await interaction.response.send_modal(SellerAgreeModal())
        else:
            await interaction.response.send_message("Only the registered buyer or seller can agree to terms.", ephemeral=True)

    @discord.ui.button(label="Mark Delivered", style=discord.ButtonStyle.primary, custom_id="automm_mark_delivered")
    async def mark_delivered(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel
        data = get_ticket(channel.id)
        if not data:
            return

        if interaction.user.id != data.get("seller_id"):
            await interaction.response.send_message("Only the seller can mark delivery complete.", ephemeral=True)
            return

        deal_id = data.get("deal_id")
        await interaction.response.defer()

        try:
            resp = await automm_client.mark_delivered(deal_id, seller_id=str(interaction.user.id))
            update_ticket(channel.id, status="DELIVERED")
            await channel.send("📦 **Seller marked delivery as complete!**\nBuyer: Please inspect your order and click **Release Funds** to complete the deal.")
            await refresh_deal_panel(channel)
        except AutoMMApiError as e:
            await interaction.followup.send(f"❌ Error marking delivered: {e.message}", ephemeral=True)

    @discord.ui.button(label="Release Funds", style=discord.ButtonStyle.success, custom_id="automm_release_funds")
    async def release_funds(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel
        data = get_ticket(channel.id)
        if not data:
            return

        # Buyer or staff can release
        if interaction.user.id != data.get("buyer_id") and not is_staff(interaction.user):
            await interaction.response.send_message("Only the buyer (or Middleman staff) can release escrow funds.", ephemeral=True)
            return

        deal_id = data.get("deal_id")
        await interaction.response.send_message("⏳ **PAYOUT PROCESSING** — Decrypting escrow key and broadcasting payout transaction to Sepolia testnet...", ephemeral=False)

        try:
            payout_resp = await automm_client.release(deal_id, requester_id=str(interaction.user.id))
            tx_hash = payout_resp.get("tx_hash")

            update_ticket(
                channel.id,
                status="COMPLETED",
                latest_tx_hash=tx_hash
            )

            await channel.send(
                f"✅ **DEAL COMPLETED!** Escrow funds released to the seller.\n"
                f"• Transaction Hash: `{tx_hash}`\n"
                f"• Etherscan Explorer: https://sepolia.etherscan.io/tx/{tx_hash}"
            )
            await refresh_deal_panel(channel)

        except AutoMMApiError as e:
            await channel.send(f"❌ Payout failed: {e.message}")

    @discord.ui.button(label="Dispute", style=discord.ButtonStyle.danger, custom_id="automm_dispute")
    async def dispute(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel
        data = get_ticket(channel.id)
        if not data:
            return

        if interaction.user.id not in (data.get("buyer_id"), data.get("seller_id")) and not is_staff(interaction.user):
            await interaction.response.send_message("Only the buyer, seller, or staff can raise a dispute.", ephemeral=True)
            return

        await interaction.response.send_modal(DisputeModal())

    @discord.ui.button(label="Staff: Refund Buyer", style=discord.ButtonStyle.secondary, custom_id="automm_staff_refund")
    async def staff_refund(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Only authorized Middleman staff can trigger an administrative refund.", ephemeral=True)
            return

        channel = interaction.channel
        data = get_ticket(channel.id)
        if not data:
            return

        deal_id = data.get("deal_id")
        await interaction.response.send_message("⏳ **REFUND PROCESSING** — Broadcasting refund transaction to Sepolia testnet...", ephemeral=False)

        try:
            refund_resp = await automm_client.refund(deal_id, requester_id=BACKEND_SECRET, reason=f"Staff refund by {interaction.user}")
            tx_hash = refund_resp.get("tx_hash")

            update_ticket(
                channel.id,
                status="REFUNDED",
                latest_tx_hash=tx_hash
            )

            await channel.send(
                f"↩️ **REFUND COMPLETED!** Escrow funds returned to the buyer.\n"
                f"• Transaction Hash: `{tx_hash}`\n"
                f"• Etherscan Explorer: https://sepolia.etherscan.io/tx/{tx_hash}"
            )
            await refresh_deal_panel(channel)

        except AutoMMApiError as e:
            await channel.send(f"❌ Refund failed: {e.message}")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="automm_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel
        data = get_ticket(channel.id)
        if not data:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return

        if interaction.user.id != data.get("buyer_id") and not is_staff(interaction.user):
            await interaction.response.send_message("Only the buyer or staff can close this ticket.", ephemeral=True)
            return

        await interaction.response.defer()

        # Build transcript
        lines = []
        async for msg in channel.history(limit=None, oldest_first=True):
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.content
            if msg.attachments:
                content += " | Attachments: " + ", ".join(a.url for a in msg.attachments)
            lines.append(f"[{timestamp}] {msg.author} ({msg.author.id}): {content}")

        buffer = io.BytesIO("\n".join(lines).encode("utf-8"))
        buffer.seek(0)
        transcript = discord.File(buffer, filename=f"transcript-{channel.name}.txt")

        if TRANSCRIPT_CHANNEL_ID:
            transcript_chan = interaction.guild.get_channel(TRANSCRIPT_CHANNEL_ID)
            if transcript_chan:
                await transcript_chan.send(
                    content=f"📁 Escrow Ticket `{channel.name}` closed by {interaction.user.mention} (Backend Deal: `{data.get('deal_id')}`)",
                    file=transcript
                )

        delete_ticket(channel.id)
        await channel.send("🔒 Ticket archived. Channel deleting in 5 seconds...")
        await asyncio.sleep(5)

        try:
            await channel.delete()
        except discord.NotFound:
            pass


# ------------------------------------------------------------
# Ticket Creation View
# ------------------------------------------------------------

class CreateTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Deal", emoji="🎫", style=discord.ButtonStyle.success, custom_id="automm_create_deal")
    async def create_deal(self, interaction: discord.Interaction, button: Button):
        # Prevent multiple active tickets per buyer
        for channel_id, data in tickets.items():
            if data.get("buyer_id") == interaction.user.id:
                existing = interaction.guild.get_channel(int(channel_id))
                if existing:
                    await interaction.response.send_message(f"You already have an active ticket: {existing.mention}", ephemeral=True)
                    return

        await interaction.response.defer(ephemeral=True)

        # 1. Initialize deal on backend
        try:
            backend_deal = await automm_client.create_deal(
                buyer_id=str(interaction.user.id),
                amount="0",
                token="ETH",
                network="SEPOLIA"
            )
            deal_id = backend_deal["id"]
        except AutoMMApiError as e:
            await interaction.followup.send(f"❌ Could not create backend escrow deal: {e.message}", ephemeral=True)
            return

        # 2. Setup channel permissions
        category = interaction.guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        }

        if MIDDLEMAN_ROLE_ID:
            middleman_role = interaction.guild.get_role(MIDDLEMAN_ROLE_ID)
            if middleman_role:
                overwrites[middleman_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)

        channel = await interaction.guild.create_text_channel(
            name=f"escrow-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            reason="AutoMM crypto escrow deal created"
        )

        update_ticket(
            channel.id,
            ticket_channel_id=channel.id,
            buyer_id=interaction.user.id,
            seller_id=None,
            deal_id=deal_id,
            status="WAITING_FOR_SELLER",
            amount="Not set",
            token="ETH",
            network="SEPOLIA",
            deposit_address=None,
            buyer_agreed=False,
            seller_agreed=False,
            terms_locked=False
        )

        embed = build_deal_embed(channel)
        msg = await channel.send(content=f"Welcome {interaction.user.mention}! Your private escrow deal room is ready.", embed=embed, view=DealRoomView())
        update_ticket(channel.id, panel_message_id=msg.id)

        await interaction.followup.send(f"🎫 Private deal room created: {channel.mention}", ephemeral=True)


# ------------------------------------------------------------
# Panel Refresh & Polling Background Task
# ------------------------------------------------------------

async def refresh_deal_panel(channel: discord.TextChannel):
    data = get_ticket(channel.id)
    if not data:
        return

    panel_msg_id = data.get("panel_message_id")
    if panel_msg_id:
        try:
            panel_msg = await channel.fetch_message(panel_msg_id)
            await panel_msg.edit(embed=build_deal_embed(channel), view=DealRoomView())
            return
        except Exception:
            pass

    async for message in channel.history(limit=50):
        if message.author.id == bot.user.id and message.embeds and "DEAL ROOM" in (message.embeds[0].title or ""):
            try:
                await message.edit(embed=build_deal_embed(channel), view=DealRoomView())
                update_ticket(channel.id, panel_message_id=message.id)
            except Exception:
                pass
            break


@tasks.loop(seconds=6.0)
async def poll_active_tickets():
    """Periodically queries backend deal status for active tickets to sync blockchain events."""
    for channel_id_str, data in list(tickets.items()):
        deal_id = data.get("deal_id")
        if not deal_id:
            continue

        current_status = data.get("status")
        # Only poll if waiting for payment or confirming
        if current_status not in ("WAITING_FOR_PAYMENT", "PAYMENT_DETECTED", "RELEASE_PENDING"):
            continue

        try:
            status_resp = await automm_client.get_status(deal_id)
            backend_status = status_resp.get("status")
            confs = status_resp.get("confirmations", 0)
            req_confs = status_resp.get("required_confirmations", 2)

            state_changed = (backend_status != current_status) or (confs != data.get("confirmations", 0))

            if state_changed:
                update_ticket(
                    int(channel_id_str),
                    status=backend_status,
                    confirmations=confs,
                    required_confirmations=req_confs
                )

                channel = bot.get_channel(int(channel_id_str))
                if channel and isinstance(channel, discord.TextChannel):
                    if backend_status == "PAYMENT_DETECTED" and current_status != "PAYMENT_DETECTED":
                        await channel.send(f"⚡ **Deposit detected on blockchain!** Confirmations: `{confs}/{req_confs}`. Waiting for required threshold...")
                    elif backend_status == "FUNDED" and current_status != "FUNDED":
                        await channel.send(f"🎉 **DEAL IS FUNDED!** On-chain deposit confirmed with `{confs}` confirmations.\nSeller can now safely deliver the agreed item/service.")

                    await refresh_deal_panel(channel)

        except Exception as e:
            logger.debug(f"Error polling ticket {channel_id_str}: {e}")


# ------------------------------------------------------------
# Discord Bot Commands & Lifecycle
# ------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="$", intents=intents)


@bot.event
async def on_ready():
    bot.add_view(CreateTicketView())
    bot.add_view(DealRoomView())
    if not poll_active_tickets.is_running():
        poll_active_tickets.start()
    logger.info(f"Connected to Discord as {bot.user} (ID: {bot.user.id})")


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_panel(ctx):
    """Post the AutoMM escrow ticket creation panel."""
    embed = discord.Embed(
        title=f"🛡️ {BRAND_NAME}",
        description=(
            "**Welcome to AutoMM Decentralized Escrow!**\n\n"
            "Create a private deal room for secure crypto trades:\n"
            "• 🤝 Buyer & Seller negotiate and lock terms\n"
            "• 🔒 Dedicated on-chain escrow address generated\n"
            "• ⛓️ Real blockchain confirmation monitoring\n"
            "• 💰 Automated release & dispute mediation\n\n"
            "Click **Create Deal** below to start a private trade."
        ),
        color=0x5865F2
    )
    embed.set_footer(text="AutoMM • Powered by Ethereum Sepolia Testnet")
    await ctx.send(embed=embed, view=CreateTicketView())


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("DISCORD_TOKEN environment variable is not set.")
    bot.run(BOT_TOKEN)

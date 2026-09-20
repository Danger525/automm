import os
import io
import json
import logging
import asyncio
from typing import Optional

import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from dotenv import load_dotenv

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

BRAND_NAME = "AutoMM Secure Escrow"
DATA_FILE = "tickets.json"

# ------------------------------------------------------------
# Simple persistent ticket storage
# ------------------------------------------------------------

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


tickets = load_data()


def get_ticket(channel_id: int):
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
# Helpers
# ------------------------------------------------------------

def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True

    return any(role.id == MIDDLEMAN_ROLE_ID for role in member.roles)


def ticket_owner(channel: discord.TextChannel) -> Optional[discord.Member]:
    data = get_ticket(channel.id)
    if not data:
        return None

    return channel.guild.get_member(data["buyer_id"])


def build_deal_embed(channel: discord.TextChannel) -> discord.Embed:
    data = get_ticket(channel.id) or {}

    buyer_id = data.get("buyer_id")
    seller_id = data.get("seller_id")

    buyer = channel.guild.get_member(buyer_id) if buyer_id else None
    seller = channel.guild.get_member(seller_id) if seller_id else None

    amount = data.get("amount") or "Not set"
    currency = data.get("currency") or "Not set"
    network = data.get("network") or "Not set"

    buyer_agreed = data.get("buyer_agreed", False)
    seller_agreed = data.get("seller_agreed", False)

    if buyer_agreed and seller_agreed:
        status = "🟢 Both parties agreed — terms locked"
    elif buyer_agreed or seller_agreed:
        status = "🟡 Waiting for the other party to agree"
    else:
        status = "🔵 Waiting for both parties to agree"

    embed = discord.Embed(
        title=f"{BRAND_NAME} — Deal Room",
        color=0x5865F2
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
        name="Status",
        value=status,
        inline=False
    )

    embed.add_field(
        name="Deal Amount",
        value=f"`{amount} {currency}`" if amount != "Not set" else "`Not set`",
        inline=True
    )

    embed.add_field(
        name="Network",
        value=f"`{network}`",
        inline=True
    )

    embed.add_field(
        name="Agreement",
        value=(
            f"Buyer: {'✅ Agreed' if buyer_agreed else '❌ Not agreed'}\n"
            f"Seller: {'✅ Agreed' if seller_agreed else '❌ Not agreed'}"
        ),
        inline=False
    )

    embed.set_footer(text="Chat in this ticket to discuss the deal.")

    return embed


# ------------------------------------------------------------
# Add Seller Modal
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
            await interaction.response.send_message(
                "This can only be used inside a ticket.",
                ephemeral=True
            )
            return

        data = get_ticket(channel.id)

        if not data:
            await interaction.response.send_message(
                "Ticket data was not found.",
                ephemeral=True
            )
            return

        if interaction.user.id != data["buyer_id"] and not is_staff(interaction.user):
            await interaction.response.send_message(
                "Only the buyer or an authorized Middleman can add the seller.",
                ephemeral=True
            )
            return

        raw = self.seller.value.strip()

        # Accept <@123>, <@!123>, or plain ID
        raw = raw.replace("<@", "").replace("!", "").replace(">", "")

        try:
            seller_id = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid Discord user ID or mention.",
                ephemeral=True
            )
            return

        seller = interaction.guild.get_member(seller_id)

        if seller is None:
            try:
                seller = await interaction.guild.fetch_member(seller_id)
            except (discord.NotFound, discord.HTTPException):
                seller = None

        if seller is None:
            await interaction.response.send_message(
                "That user is not a member of this server.",
                ephemeral=True
            )
            return

        if seller.id == data["buyer_id"]:
            await interaction.response.send_message(
                "The buyer cannot be the seller.",
                ephemeral=True
            )
            return

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
            seller_agreed=False
        )

        await interaction.response.send_message(
            f"✅ {seller.mention} has been added as the seller."
        )

        await refresh_deal_panel(channel)


# ------------------------------------------------------------
# Set Deal Modal
# ------------------------------------------------------------

class SetDealModal(Modal, title="Set Deal Terms"):
    amount = TextInput(
        label="Amount",
        placeholder="Example: 500",
        required=True,
        max_length=50
    )

    currency = TextInput(
        label="Currency",
        placeholder="Example: USDT",
        required=True,
        max_length=20
    )

    network = TextInput(
        label="Network",
        placeholder="Example: Polygon",
        required=True,
        max_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return

        data = get_ticket(channel.id)

        if not data:
            await interaction.response.send_message(
                "Ticket data was not found.",
                ephemeral=True
            )
            return

        if interaction.user.id != data["buyer_id"] and not is_staff(interaction.user):
            await interaction.response.send_message(
                "Only the buyer or authorized staff can set the deal terms.",
                ephemeral=True
            )
            return

        if data.get("buyer_agreed") and data.get("seller_agreed"):
            await interaction.response.send_message(
                "The deal terms are locked because both parties agreed. To renegotiate, both parties must reset.",
                ephemeral=True
            )
            return

        if not data.get("seller_id"):
            await interaction.response.send_message(
                "Add the seller before setting the deal.",
                ephemeral=True
            )
            return

        update_ticket(
            channel.id,
            amount=self.amount.value.strip(),
            currency=self.currency.value.strip().upper(),
            network=self.network.value.strip(),
            buyer_agreed=False,
            seller_agreed=False
        )

        await interaction.response.send_message(
            "✅ Deal terms have been updated. Both parties must agree."
        )

        await refresh_deal_panel(channel)


# ------------------------------------------------------------
# Deal Room Buttons
# ------------------------------------------------------------

class DealRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Add Seller",
        style=discord.ButtonStyle.primary,
        custom_id="automm_add_seller"
    )
    async def add_seller(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddSellerModal())

    @discord.ui.button(
        label="Set Deal",
        style=discord.ButtonStyle.secondary,
        custom_id="automm_set_deal"
    )
    async def set_deal(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SetDealModal())

    @discord.ui.button(
        label="Agree",
        style=discord.ButtonStyle.success,
        custom_id="automm_agree"
    )
    async def agree(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return

        data = get_ticket(channel.id)

        if not data:
            await interaction.response.send_message(
                "Ticket data was not found.",
                ephemeral=True
            )
            return

        if not data.get("seller_id"):
            await interaction.response.send_message(
                "A seller must be added first.",
                ephemeral=True
            )
            return

        if not data.get("amount"):
            await interaction.response.send_message(
                "Deal terms must be set first.",
                ephemeral=True
            )
            return

        if interaction.user.id == data["buyer_id"]:
            data["buyer_agreed"] = True
        elif interaction.user.id == data["seller_id"]:
            data["seller_agreed"] = True
        else:
            await interaction.response.send_message(
                "Only the buyer or seller can agree to the deal.",
                ephemeral=True
            )
            return

        update_ticket(channel.id, **{
            "buyer_agreed": data.get("buyer_agreed", False),
            "seller_agreed": data.get("seller_agreed", False)
        })

        if data.get("buyer_agreed") and data.get("seller_agreed"):
            await interaction.response.send_message(
                "🔒 Both parties agreed. The deal terms are now locked."
            )
        else:
            await interaction.response.send_message(
                "✅ Your agreement has been recorded. Waiting for the other party."
            )

        await refresh_deal_panel(channel)

    @discord.ui.button(
        label="Dispute",
        style=discord.ButtonStyle.danger,
        custom_id="automm_dispute"
    )
    async def dispute(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel

        data = get_ticket(channel.id)

        if not data:
            await interaction.response.send_message(
                "Ticket data was not found.",
                ephemeral=True
            )
            return

        update_ticket(channel.id, status="DISPUTED")

        await interaction.response.send_message(
            f"⚖️ {interaction.user.mention} opened a dispute. "
            "An authorized Middleman should review this ticket."
        )

        await refresh_deal_panel(channel)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="automm_close_ticket"
    )
    async def close(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel

        data = get_ticket(channel.id)

        if not data:
            await interaction.response.send_message(
                "Ticket data was not found.",
                ephemeral=True
            )
            return

        if interaction.user.id != data["buyer_id"] and not is_staff(interaction.user):
            await interaction.response.send_message(
                "Only the buyer or authorized staff can close this ticket.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # Create transcript
        lines = []

        async for msg in channel.history(limit=None, oldest_first=True):
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.content

            if msg.attachments:
                content += " | Attachments: " + ", ".join(
                    attachment.url for attachment in msg.attachments
                )

            lines.append(
                f"[{timestamp}] {msg.author} ({msg.author.id}): {content}"
            )

        buffer = io.BytesIO("\n".join(lines).encode("utf-8"))
        buffer.seek(0)

        transcript = discord.File(
            buffer,
            filename=f"transcript-{channel.name}.txt"
        )

        if TRANSCRIPT_CHANNEL_ID:
            transcript_channel = interaction.guild.get_channel(
                TRANSCRIPT_CHANNEL_ID
            )

            if transcript_channel:
                await transcript_channel.send(
                    content=(
                        f"📁 Ticket `{channel.name}` closed by "
                        f"{interaction.user.mention}"
                    ),
                    file=transcript
                )

        delete_ticket(channel.id)

        await channel.send("🔒 Ticket archived. Closing in 5 seconds...")
        await asyncio.sleep(5)

        try:
            await channel.delete()
        except discord.NotFound:
            pass


# ------------------------------------------------------------
# Ticket Creation
# ------------------------------------------------------------

class CreateTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="automm_create_ticket"
    )
    async def create_ticket(self, interaction: discord.Interaction, button: Button):

        # Prevent duplicate open ticket
        for channel_id, data in tickets.items():
            if data.get("buyer_id") == interaction.user.id:
                existing = interaction.guild.get_channel(int(channel_id))
                if existing:
                    await interaction.response.send_message(
                        f"You already have an open ticket: {existing.mention}",
                        ephemeral=True
                    )
                    return

        category = None

        if TICKET_CATEGORY_ID:
            category = interaction.guild.get_channel(TICKET_CATEGORY_ID)

        overwrites = {
            interaction.guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            interaction.user:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True
                ),

            interaction.guild.me:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True
                )
        }

        # Middleman role
        if MIDDLEMAN_ROLE_ID:
            middleman_role = interaction.guild.get_role(MIDDLEMAN_ROLE_ID)

            if middleman_role:
                overwrites[middleman_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True
                )

        channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            reason="AutoMM escrow ticket created"
        )

        embed = discord.Embed(
            title=f"{BRAND_NAME} — Deal Room",
            description=(
                "Your private escrow deal room is ready.\n\n"
                "**Next steps:**\n"
                "1. Add the seller.\n"
                "2. Set the deal amount and network.\n"
                "3. Discuss the deal in this channel.\n"
                "4. Both buyer and seller must click **Agree**.\n\n"
                "The escrow wallet should only be created after both "
                "parties agree to the final terms."
            ),
            color=0x5865F2
        )

        embed.add_field(
            name="Buyer",
            value=interaction.user.mention,
            inline=True
        )

        embed.add_field(
            name="Seller",
            value="Waiting for seller...",
            inline=True
        )

        embed.set_footer(text="AutoMM • Private Deal Room")

        panel_msg = await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=DealRoomView()
        )

        update_ticket(
            channel.id,
            buyer_id=interaction.user.id,
            seller_id=None,
            amount=None,
            currency=None,
            network=None,
            buyer_agreed=False,
            seller_agreed=False,
            status="WAITING_FOR_SELLER",
            panel_message_id=panel_msg.id
        )

        await interaction.response.send_message(
            f"🎫 Your ticket has been created: {channel.mention}",
            ephemeral=True
        )


# ------------------------------------------------------------
# Bot
# ------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="$",
    intents=intents
)


@bot.event
async def on_ready():
    bot.add_view(CreateTicketView())
    bot.add_view(DealRoomView())

    logger.info(
        f"Connected to Discord as {bot.user} (ID: {bot.user.id})"
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_panel(ctx):
    """Create the AutoMM ticket panel."""

    embed = discord.Embed(
        title=f"{BRAND_NAME}",
        description=(
            "Create a private escrow deal room for your trade.\n\n"
            "Inside the ticket, both parties can:\n"
            "• Chat privately\n"
            "• Add the seller\n"
            "• Set deal terms\n"
            "• Agree to the final deal\n"
            "• Open a dispute\n"
            "• Close and archive the ticket"
        ),
        color=0x5865F2
    )

    embed.set_footer(text="AutoMM • Secure Deal Room")

    await ctx.send(
        embed=embed,
        view=CreateTicketView()
    )


@bot.command()
async def add(ctx, member: discord.Member):
    """Legacy/manual seller adding command."""

    if not isinstance(ctx.channel, discord.TextChannel):
        return

    data = get_ticket(ctx.channel.id)

    if not data:
        await ctx.send("This is not an AutoMM ticket.")
        return

    if ctx.author.id != data["buyer_id"] and not is_staff(ctx.author):
        await ctx.send("Only the buyer or staff can add the seller.")
        return

    if member.id == data["buyer_id"]:
        await ctx.send("The buyer cannot be the seller.")
        return

    await ctx.channel.set_permissions(
        member,
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True
    )

    update_ticket(ctx.channel.id, seller_id=member.id)

    await ctx.send(f"✅ {member.mention} has been added as the seller.")
    await refresh_deal_panel(ctx.channel)


async def refresh_deal_panel(channel: discord.TextChannel):
    """Update the deal room panel in the ticket."""

    data = get_ticket(channel.id)

    if not data:
        return

    panel_msg_id = data.get("panel_message_id")
    if panel_msg_id:
        try:
            panel_msg = await channel.fetch_message(panel_msg_id)
            await panel_msg.edit(
                embed=build_deal_embed(channel),
                view=DealRoomView()
            )
            return
        except (discord.NotFound, discord.HTTPException):
            pass

    async for message in channel.history(limit=50):
        if (
            message.author.id == bot.user.id
            and message.embeds
            and message.embeds[0].title
            and "Deal Room" in message.embeds[0].title
        ):
            try:
                await message.edit(
                    embed=build_deal_embed(channel),
                    view=DealRoomView()
                )
                update_ticket(channel.id, panel_message_id=message.id)
            except discord.NotFound:
                pass

            break


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need Administrator permissions for this command.")
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`")
        return

    logger.error("Command error: %s", error)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN environment variable is not set in environment or .env file."
        )

    bot.run(BOT_TOKEN)

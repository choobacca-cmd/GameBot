import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select
import sqlite3
import random
import asyncio
from typing import List, Dict, Optional, Tuple
from discord.ui import View as DiscordView

# Database setup
def init_db():
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    # Drop old table if exists
    c.execute("DROP TABLE IF EXISTS queue")
    
    # Create new tables with required columns
    c.execute('''CREATE TABLE queue
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  queue_type TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS players
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT, 
                  elo INTEGER DEFAULT 0,
                  wins INTEGER DEFAULT 0,
                  losses INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  team_a_players TEXT,
                  team_b_players TEXT,
                  winning_team TEXT,
                  map_played TEXT,
                  disputed BOOLEAN DEFAULT FALSE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS level_roles
                 (level INTEGER PRIMARY KEY,
                  role_id INTEGER)''')
    
    conn.commit()
    conn.close()

init_db()

# Queue types configuration
QUEUE_TYPES = {
    "2v2": {"team_size": 2, "total_players": 4},
    "3v3": {"team_size": 3, "total_players": 6}, 
    "4v4": {"team_size": 4, "total_players": 8},
    "5v5": {"team_size": 5, "total_players": 10}
}

# ELO Level Configuration with role IDs
ELO_LEVELS = {
    1: {"min_elo": 0, "role_id": 1396073017959911425},
    2: {"min_elo": 100, "role_id": 1396073678109806722},
    3: {"min_elo": 200, "role_id": 1396073995744448603},
    4: {"min_elo": 350, "role_id": 1396074130776002711},
    5: {"min_elo": 450, "role_id": 1396074186296004758},
    6: {"min_elo": 600, "role_id": 1396074230579200010},
    7: {"min_elo": 800, "role_id": 1396074267308855376},
    8: {"min_elo": 1000, "role_id": 1396074325236514967},
    9: {"min_elo": 1300, "role_id": 1396074433470529627},
    10: {"min_elo": 2000, "role_id": 1396074513568895099}
}

# Constants for channel IDs
ADMIN_RESULTS_CHANNEL = 1398642394257428522
ADMIN_CHANNEL = 1397957346604351508
LEADERBOARD_CHANNEL = 1397979720645349549

class QueueSelectView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

        options = [
            discord.SelectOption(label="2v2", description="2 players per team", value="2v2"),
            discord.SelectOption(label="3v3", description="3 players per team", value="3v3"),
            discord.SelectOption(label="4v4", description="4 players per team", value="4v4"),
            discord.SelectOption(label="5v5", description="5 players per team", value="5v5")
        ]

        select = Select(
            placeholder="Select queue type",
            options=options,
            custom_id="queue_type_select"
        )
        select.callback = self.queue_selected
        self.add_item(select)

    async def queue_selected(self, interaction: discord.Interaction):
        queue_type = interaction.data["values"][0]

        embed = discord.Embed(
            title=f"⚔️ {queue_type} Matchmaking Queue",
            description=f"Click the buttons below to join or leave the {queue_type} queue",
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Waiting for players...")

        view = MatchmakingView(self.bot, queue_type)
        await interaction.response.send_message(embed=embed, view=view)
        view.queue_message = await interaction.original_response()
        await view.update_queue_embed()


class MatchmakingView(View):
    def __init__(self, bot, queue_type="4v4"):
        super().__init__(timeout=None)
        self.bot = bot
        self.queue_type = queue_type
        self.queue_message = None
    
    async def update_queue_embed(self):
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM queue WHERE queue_type=?", (self.queue_type,))
        queue_size = c.fetchone()[0]
        required = QUEUE_TYPES[self.queue_type]["total_players"]
        
        c.execute("SELECT username FROM queue WHERE queue_type=?", (self.queue_type,))
        players = c.fetchall()
        player_list = "\n".join([f"• {p[0]}" for p in players]) if players else "No players yet"
        conn.close()
        
        embed = discord.Embed(
            title=f"⚔️ {self.queue_type} Matchmaking Queue",
            description=f"**Status:** {queue_size}/{required} players ready",
            color=discord.Color.green() if queue_size >= required else discord.Color.orange()
        )
        embed.add_field(name="Players in queue:", value=player_list, inline=False)
        embed.set_footer(text=f"Queue type: {self.queue_type} | Use buttons to join/leave")
        
        if self.queue_message:
            try:
                await self.queue_message.edit(embed=embed, view=self)
            except Exception as e:
                print(f"Error updating queue embed: {e}")
    
    @discord.ui.button(label="Join Queue", style=discord.ButtonStyle.green, custom_id="join_queue")
    async def join_queue(self, interaction: discord.Interaction, button: Button):
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        
        # Remove player from all queues first
        c.execute("DELETE FROM queue WHERE user_id=?", (interaction.user.id,))
        
        # Add player to this queue
        c.execute("INSERT INTO queue (user_id, username, queue_type) VALUES (?, ?, ?)",
                  (interaction.user.id, str(interaction.user), self.queue_type))
        conn.commit()
        conn.close()
        
        await self.update_queue_embed()
        await interaction.response.send_message(f"You joined {self.queue_type} queue!", ephemeral=True)
        
        # Check if queue is full and start match
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM queue WHERE queue_type=?", (self.queue_type,))
        queue_size = c.fetchone()[0]
        conn.close()
        
        if queue_size >= QUEUE_TYPES[self.queue_type]["total_players"]:
            await self.start_match(interaction)
    
    @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.red, custom_id="leave_queue")
    async def leave_queue(self, interaction: discord.Interaction, button: Button):
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        
        c.execute("DELETE FROM queue WHERE user_id=?", (interaction.user.id,))
        conn.commit()
        conn.close()
        
        await self.update_queue_embed()
        await interaction.response.send_message(f"You left {self.queue_type} queue.", ephemeral=True)
    
    async def get_reaction_users(self, reaction):
        users = []
        async for user in reaction.users():
            if user != self.bot.user:
                users.append(user)
        return users
    
    async def start_vote(self, channel, title, description, options, timeout=30):
        if not options:
            return None
            
        embed = discord.Embed(title=title, description=description, color=0x3498db)
        
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, option in enumerate(options[:len(emojis)]):
            embed.add_field(name=f"{emojis[i]} {option}", value="\u200b", inline=False)
        
        msg = await channel.send(embed=embed)
        
        for i in range(len(options[:len(emojis)])):
            await msg.add_reaction(emojis[i])
        
        await asyncio.sleep(timeout)
        
        try:
            msg = await channel.fetch_message(msg.id)
            reactions = msg.reactions
            
            votes = []
            for i, reaction in enumerate(reactions):
                if i >= len(options):
                    continue
                users = await self.get_reaction_users(reaction)
                votes.append((options[i], len(users)))
            
            return max(votes, key=lambda x: x[1])[0] if votes else random.choice(options)
        except Exception as e:
            print(f"Voting error: {e}")
            return random.choice(options)
    
    async def update_player_role(self, guild, player_id, new_elo):
        member = guild.get_member(player_id)
        if not member:
            return
        
        # Remove old level roles
        for level_data in ELO_LEVELS.values():
            if level_data["role_id"]:
                role = guild.get_role(level_data["role_id"])
                if role and role in member.roles:
                    await member.remove_roles(role)
        
        # Add new role based on new elo
        for level, level_data in sorted(ELO_LEVELS.items(), key=lambda x: x[0], reverse=True):
            if new_elo >= level_data["min_elo"]:
                if level_data["role_id"]:
                    role = guild.get_role(level_data["role_id"])
                    if role:
                        await member.add_roles(role)
                break
    
    async def start_match(self, interaction: discord.Interaction):
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        
        c.execute("SELECT * FROM queue WHERE queue_type=?", (self.queue_type,))
        queue_players = c.fetchall()
        
        if len(queue_players) < QUEUE_TYPES[self.queue_type]["total_players"]:
            await interaction.followup.send("Not enough players in queue!", ephemeral=True)
            conn.close()
            return
        
        c.execute("DELETE FROM queue WHERE queue_type=?", (self.queue_type,))
        conn.commit()
        conn.close()
        
        await self.update_queue_embed()
        
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }
        
        for player_id, _, _ in queue_players:
            member = guild.get_member(player_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(read_messages=True)
        
        match_channel = await guild.create_text_channel(
            name=f"match-{self.queue_type}-{random.randint(1000, 9999)}",
            overwrites=overwrites
        )
        
        try:
            player_names = [p[1] for p in queue_players]
            player_ids = [p[0] for p in queue_players]
            
            # Captain voting
            captains = await self.start_vote(
                match_channel,
                "🛡️ Captain Voting",
                "Vote for 2 captains:",
                player_names
            )
            
            if isinstance(captains, str):
                # If only one captain was voted, pick another random one
                remaining_players = [p for p in player_names if p != captains]
                captains = [captains, random.choice(remaining_players)]
            else:
                # Fallback to random selection if voting failed
                captains = random.sample(player_names, 2)
            
            captain_ids = [queue_players[player_names.index(name)][0] for name in captains]
            
            # Team pick style vote
            pick_style = await self.start_vote(
                match_channel,
                "⚙️ Team Pick Style",
                "Vote for team selection style:",
                ["Team Pick (captains choose)", "Random Teams"]
            )
            
            team_a = [captain_ids[0]]
            team_b = [captain_ids[1]]
            remaining_players = [p[0] for p in queue_players if p[0] not in captain_ids]
            
            if pick_style == "Team Pick (captains choose)":
                turn = 0
                while remaining_players:
                    current_captain = team_a[0] if turn == 0 else team_b[0]
                    captain_member = guild.get_member(current_captain)
                    
                    if captain_member:
                        embed = discord.Embed(
                            title=f"Team {'A' if turn == 0 else 'B'} Captain's Turn",
                            description=f"<@{current_captain}>, pick a player by reacting:",
                            color=0x3498db
                        )
                        
                        options = [f"<@{p}>" for p in remaining_players]
                        for i, option in enumerate(options):
                            embed.add_field(name=f"{i+1}. {option}", value="\u200b", inline=False)
                        
                        pick_msg = await match_channel.send(embed=embed)
                        
                        for i in range(len(options)):
                            await pick_msg.add_reaction(f"{i+1}\N{variation selector-16}\N{combining enclosing keycap}")
                        
                        def check(reaction, user):
                            return user.id == current_captain and str(reaction.emoji) in [f"{i+1}\N{variation selector-16}\N{combining enclosing keycap}" for i in range(len(options))]
                        
                        try:
                            reaction, _ = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
                            picked_index = int(str(reaction.emoji)[0]) - 1
                            picked_player = remaining_players[picked_index]
                            
                            if turn == 0:
                                team_a.append(picked_player)
                            else:
                                team_b.append(picked_player)
                            
                            remaining_players.remove(picked_player)
                            turn = 1 - turn
                            
                            await match_channel.send(f"<@{current_captain}> picked <@{picked_player}>!")
                        except asyncio.TimeoutError:
                            picked_player = random.choice(remaining_players)
                            if turn == 0:
                                team_a.append(picked_player)
                            else:
                                team_b.append(picked_player)
                            
                            remaining_players.remove(picked_player)
                            turn = 1 - turn
                            
                            await match_channel.send(f"Captain didn't pick in time! Randomly assigned <@{picked_player}>.")
            else:
                random.shuffle(remaining_players)
                for i, p in enumerate(remaining_players):
                    if i % 2 == 0:
                        team_a.append(p)
                    else:
                        team_b.append(p)
            
            maps = ["Urban", "Air Force", "Sandstorm", "Rampage", "District", "Iraq", "Morocco"]
            selected_map = await self.start_vote(
                match_channel,
                "🗺️ Map Voting",
                "Vote for the map:",
                maps
            )
            
            creator_vote = await self.start_vote(
                match_channel,
                "👑 Room Creator Voting",
                "Vote for room creator:",
                player_names
            )
            creator_id = queue_players[player_names.index(creator_vote)][0] if creator_vote in player_names else queue_players[0][0]
            
            password = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=6))
            
            team_a_channel = await guild.create_voice_channel(name=f"Team A - {selected_map}", overwrites=overwrites)
            team_b_channel = await guild.create_voice_channel(name=f"Team B - {selected_map}", overwrites=overwrites)
            
            for player_id in team_a:
                member = guild.get_member(player_id)
                if member and member.voice:
                    try:
                        await member.move_to(team_a_channel)
                    except:
                        pass
            
            for player_id in team_b:
                member = guild.get_member(player_id)
                if member and member.voice:
                    try:
                        await member.move_to(team_b_channel)
                    except:
                        pass
            
            embed = discord.Embed(
                title="🎮 Match Ready!",
                description=f"**Map:** {selected_map} | **Mode:** {self.queue_type}",
                color=0xf1c40f
            )
            embed.add_field(name="Team A", value="\n".join([f"<@{p}>" for p in team_a]), inline=True)
            embed.add_field(name="Team B", value="\n".join([f"<@{p}>" for p in team_b]), inline=True)
            embed.add_field(
                name="Match Details",
                value=f"**Room Creator:** <@{creator_id}>\n**Password:** ||{password}||\nVoice Channels:\n- {team_a_channel.mention}\n- {team_b_channel.mention}",
                inline=False
            )
            
            await match_channel.send(embed=embed)
            
            # Save match to database
            conn = sqlite3.connect('elo_bot.db')
            c = conn.cursor()
            c.execute(
                "INSERT INTO matches (team_a_players, team_b_players, map_played) VALUES (?, ?, ?)",
                (','.join(map(str, team_a)), ','.join(map(str, team_b)), selected_map)
            )
            match_id = c.lastrowid
            conn.commit()
            conn.close()
            
            voting_view = MatchResultView(
                bot=self.bot,
                match_id=match_id,
                team_a=team_a,
                team_b=team_b,
                match_channel=match_channel,
                team_a_channel=team_a_channel,
                team_b_channel=team_b_channel,
                admin_channel=ADMIN_CHANNEL,
                leaderboard_channel=LEADERBOARD_CHANNEL,
                admin_results=ADMIN_RESULTS_CHANNEL
            )
            
            await match_channel.send(embed=discord.Embed(
                title="🏆 Match Result Voting",
                description="Vote for the winning team:",
                color=0x3498db
            ), view=voting_view)
            
        except Exception as e:
            print(f"Matchmaking error: {e}")
            try:
                await match_channel.send(f"❌ Error occurred: {str(e)}")
            except:
                await interaction.followup.send(f"❌ Error occurred: {str(e)}", ephemeral=True)


class MatchResultView(View):
    def __init__(self, bot, match_id, team_a, team_b, match_channel, team_a_channel, team_b_channel, admin_channel=None, leaderboard_channel=None, admin_results=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.match_id = match_id
        self.team_a = team_a
        self.team_b = team_b
        self.match_channel = match_channel
        self.team_a_channel = team_a_channel
        self.team_b_channel = team_b_channel
        self.admin_channel = admin_channel
        self.leaderboard_channel = leaderboard_channel
        self.admin_results = admin_results
    
    async def process_result(self, interaction: discord.Interaction, winning_team: str):
        # Check if user was in the match
        if interaction.user.id not in self.team_a + self.team_b:
            await interaction.response.send_message("You weren't in this match!", ephemeral=True)
            return
        
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        
        c.execute("SELECT winning_team FROM matches WHERE match_id=?", (self.match_id,))
        result = c.fetchone()
        if result and result[0] is not None:
            await interaction.response.send_message("Result already recorded!", ephemeral=True)
            conn.close()
            return
        
        # Update match result
        c.execute(
            "UPDATE matches SET winning_team=? WHERE match_id=?",
            (winning_team, self.match_id)
        )
        
        # Determine winners and losers
        winners = self.team_a if winning_team == "A" else self.team_b
        losers = self.team_b if winning_team == "A" else self.team_a
        
        # Update player ELO and stats
        for player_id in winners:
            c.execute(
                "UPDATE players SET elo=elo+25, wins=wins+1 WHERE user_id=?",
                (player_id,)
            )
            c.execute("SELECT elo FROM players WHERE user_id=?", (player_id,))
            new_elo = c.fetchone()[0]
            await self.update_player_role(interaction.guild, player_id, new_elo)
        
        for player_id in losers:
            c.execute(
                "UPDATE players SET elo=elo-25, losses=losses+1 WHERE user_id=?",
                (player_id,)
            )
            c.execute("SELECT elo FROM players WHERE user_id=?", (player_id,))
            new_elo = c.fetchone()[0]
            await self.update_player_role(interaction.guild, player_id, new_elo)
        
        conn.commit()
        
        # Send results to admin channel
        if self.admin_channel:
            c.execute("SELECT map_played FROM matches WHERE match_id=?", (self.match_id,))
            map_played = c.fetchone()[0]
            
            embed = discord.Embed(
                title="🏆 Match Results",
                color=0x00ff00
            )
            
            embed.add_field(
                name=f"Team {winning_team} Won",
                value=f"**Map:** {map_played}",
                inline=False
            )
            
            # Team A text with ELO changes
            team_a_text = []
            for p in self.team_a:
                c.execute("SELECT username, elo FROM players WHERE user_id=?", (p,))
                row = c.fetchone()
                if row:
                    username, elo = row
                    change = "+25" if p in winners else "-25"
                    team_a_text.append(f"{username} - {elo} ({change})")
            
            # Team B text with ELO changes
            team_b_text = []
            for p in self.team_b:
                c.execute("SELECT username, elo FROM players WHERE user_id=?", (p,))
                row = c.fetchone()
                if row:
                    username, elo = row
                    change = "+25" if p in winners else "-25"
                    team_b_text.append(f"{username} - {elo} ({change})")
            
            embed.add_field(name="Team A", value="\n".join(team_a_text), inline=True)
            embed.add_field(name="Team B", value="\n".join(team_b_text), inline=True)
            
            admin_channel_obj = interaction.guild.get_channel(self.admin_channel)
            if admin_channel_obj:
                await admin_channel_obj.send(embed=embed)
            
            # Update leaderboard
            if self.leaderboard_channel:
                await self.update_leaderboard(self.leaderboard_channel)
        
        conn.close()
        
        # Delete match channels
        try:
            await self.match_channel.delete()
            await self.team_a_channel.delete()
            await self.team_b_channel.delete()
        except Exception as e:
            print(f"Error deleting channels: {e}")
        
        await interaction.response.send_message(
            f"Result recorded! Team {winning_team} wins. ELO has been updated.",
            ephemeral=True
        )
    
    async def update_player_role(self, guild, player_id, new_elo):
        member = guild.get_member(player_id)
        if not member:
            return
        
        # Remove all old level roles
        for level_data in ELO_LEVELS.values():
            if level_data["role_id"]:
                role = guild.get_role(level_data["role_id"])
                if role and role in member.roles:
                    await member.remove_roles(role)
        
        # Add new role based on ELO (sorted in descending order)
        for level in sorted(ELO_LEVELS.keys(), reverse=True):
            level_data = ELO_LEVELS[level]
            if new_elo >= level_data["min_elo"]:
                if level_data["role_id"]:
                    role = guild.get_role(level_data["role_id"])
                    if role:
                        await member.add_roles(role)
                break
    
    async def update_leaderboard(self, channel_id):
        guild = self.bot.get_guild(ADMIN_CHANNEL) if ADMIN_CHANNEL else None
        if not guild:
            return
            
        channel = guild.get_channel(channel_id)
        if not channel:
            return
            
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        
        c.execute("SELECT username, elo, wins, losses FROM players ORDER BY elo DESC LIMIT 10")
        top_players = c.fetchall()
        conn.close()
        
        embed = discord.Embed(
            title="🏆 Leaderboard - Top 10 Players",
            color=0xffd700
        )
        
        for i, (username, elo, wins, losses) in enumerate(top_players, 1):
            win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
            embed.add_field(
                name=f"{i}. {username}",
                value=f"ELO: {elo}\nW/L: {wins}/{losses} ({win_rate:.1f}%)",
                inline=False
            )
        
        # Delete old leaderboard messages
        async for message in channel.history(limit=10):
            if message.author == self.bot.user and message.embeds:
                if message.embeds[0].title.startswith("🏆 Leaderboard"):
                    try:
                        await message.delete()
                    except:
                        pass
        
        await channel.send(embed=embed)
    
    @discord.ui.button(label="Team A Won", style=discord.ButtonStyle.green)
    async def team_a_won(self, interaction: discord.Interaction, button: Button):
        await self.process_result(interaction, "A")
    
    @discord.ui.button(label="Team B Won", style=discord.ButtonStyle.red)
    async def team_b_won(self, interaction: discord.Interaction, button: Button):
        await self.process_result(interaction, "B")
    
    @discord.ui.button(label="Dispute Result", style=discord.ButtonStyle.gray)
    async def dispute_result(self, interaction: discord.Interaction, button: Button):
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        c.execute("UPDATE matches SET disputed=1 WHERE match_id=?", (self.match_id,))
        conn.commit()
        conn.close()
        
        if self.admin_results and self.admin_channel:
            guild = interaction.guild
            admin_channel = guild.get_channel(self.admin_channel)
            admin_results_role = guild.get_role(self.admin_results) if self.admin_results else None
            
            if admin_channel:
                embed = discord.Embed(
                    title="⚠️ Match Result Disputed",
                    description=f"Match ID: {self.match_id}\nDisputed by: {interaction.user.mention}",
                    color=0xff0000
                )
                
                embed.add_field(
                    name="Team A",
                    value="\n".join([f"<@{p}>" for p in self.team_a]),
                    inline=True
                )
                
                embed.add_field(
                    name="Team B",
                    value="\n".join([f"<@{p}>" for p in self.team_b]),
                    inline=True
                )
                
                admin_view = AdminMatchResultView(
                    self.bot, self.match_id, self.team_a, self.team_b,
                    self.match_channel, self.team_a_channel, self.team_b_channel,
                    self.admin_channel, self.leaderboard_channel, self.admin_results
                )
                
                message = await admin_channel.send(
                    content=admin_results_role.mention if admin_results_role else "",
                    embed=embed,
                    view=admin_view
                )
                
                await interaction.response.send_message(
                    "The match result has been disputed and sent to admins for review.",
                    ephemeral=True
                )
                return
        
        await interaction.response.send_message(
            "No admin channel found to send dispute to!",
            ephemeral=True
        )


class AdminMatchResultView(View):
    def __init__(self, bot, match_id, team_a, team_b, match_channel, team_a_channel, team_b_channel, admin_channel=None, leaderboard_channel=None, admin_results=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.match_id = match_id
        self.team_a = team_a
        self.team_b = team_b
        self.match_channel = match_channel
        self.team_a_channel = team_a_channel
        self.team_b_channel = team_b_channel
        self.admin_channel = admin_channel
        self.leaderboard_channel = leaderboard_channel
        self.admin_results = admin_results
    
    async def _process_admin_result(self, interaction: discord.Interaction, winning_team: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You don't have permission!", ephemeral=True)
            return
        
        # Create MatchResultView instance and call process_result
        match_view = MatchResultView(
            self.bot,
            self.match_id,
            self.team_a,
            self.team_b,
            self.match_channel,
            self.team_a_channel,
            self.team_b_channel,
            admin_channel=self.admin_channel,
            leaderboard_channel=self.leaderboard_channel,
            admin_results=self.admin_results
        )
        await match_view.process_result(interaction, winning_team)
    
    @discord.ui.button(label="Confirm Team A Win", style=discord.ButtonStyle.green)
    async def confirm_team_a(self, interaction: discord.Interaction, button: Button):
        await self._process_admin_result(interaction, "A")
    
    @discord.ui.button(label="Confirm Team B Win", style=discord.ButtonStyle.red)
    async def confirm_team_b(self, interaction: discord.Interaction, button: Button):
        await self._process_admin_result(interaction, "B")


class EloBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        # Initialize level roles
        for guild in self.guilds:
            for level, level_data in ELO_LEVELS.items():
                role = guild.get_role(level_data["role_id"])
                if not role:
                    try:
                        role = await guild.create_role(name=f"Level {level}")
                        ELO_LEVELS[level]["role_id"] = role.id
                        
                        # Store role ID in database
                        conn = sqlite3.connect('elo_bot.db')
                        c = conn.cursor()
                        c.execute(
                            "INSERT OR REPLACE INTO level_roles VALUES (?, ?)",
                            (level, role.id)
                        )
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        print(f"Error creating role Level {level}: {e}")
        
        self.add_view(QueueSelectView(self))
    
    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        
        # Initialize database
        init_db()
        
        # Load role IDs from database
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        c.execute("SELECT * FROM level_roles")
        for level, role_id in c.fetchall():
            if level in ELO_LEVELS:
                ELO_LEVELS[level]["role_id"] = role_id
        conn.close()
        
        # Sync commands
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} commands")
        except Exception as e:
            print(f"Error syncing commands: {e}")

bot = EloBot()

@bot.tree.command(name="register", description="Register in the ELO system")
async def register(interaction: discord.Interaction):
    # Role IDs (replace with your actual IDs)
    REGISTERED_ROLE_ID = 1396071696922050622  # Replace with actual "Registered" role ID
    UNREGISTERED_ROLE_ID = 1396072475053265008  # Replace with unregistered role ID if exists

    try:
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()

        # Check if player is already registered
        c.execute("SELECT * FROM players WHERE user_id=?", (interaction.user.id,))
        if c.fetchone():
            await interaction.response.send_message("⚠️ You're already registered!", ephemeral=True)
            conn.close()
            return

        # Add player to database with 0 ELO
        c.execute(
            "INSERT INTO players (user_id, username, elo, wins, losses) VALUES (?, ?, 0, 0, 0)",
            (interaction.user.id, str(interaction.user))
        )
        conn.commit()
        conn.close()

        # Get role objects
        registered_role = interaction.guild.get_role(REGISTERED_ROLE_ID)
        unregistered_role = interaction.guild.get_role(UNREGISTERED_ROLE_ID) if UNREGISTERED_ROLE_ID else None

        # Change roles
        try:
            if unregistered_role and unregistered_role in interaction.user.roles:
                await interaction.user.remove_roles(unregistered_role)
            
            if registered_role:
                await interaction.user.add_roles(registered_role)
        except discord.Forbidden:
            print(f"Missing permissions to manage roles for {interaction.user}")
        except discord.HTTPException as e:
            print(f"Error changing roles: {e}")

        # Confirm registration
        await interaction.response.send_message(
            "✅ Successfully registered! You now have 0 ELO and access to matchmaking.",
            ephemeral=True
        )

        # Additional debug logging
        print(f"New player registered: {interaction.user} (ID: {interaction.user.id})")

    except sqlite3.Error as e:
        print(f"Database error during registration: {e}")
        await interaction.response.send_message(
            "❌ Database error during registration. Please try again or contact admin.",
            ephemeral=True
        )
        if conn:
            conn.close()

    except Exception as e:
        print(f"Unexpected error in register command: {e}")
        await interaction.response.send_message(
            "❌ An unexpected error occurred. Please contact admin.",
            ephemeral=True
        )

@bot.tree.command(name="leaderboard", description="Show top 10 players by ELO")
async def leaderboard(interaction: discord.Interaction):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    
    c.execute("SELECT username, elo, wins, losses FROM players ORDER BY elo DESC LIMIT 10")
    top_players = c.fetchall()
    conn.close()
    
    if not top_players:
        await interaction.response.send_message("No players registered yet!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🏆 Leaderboard - Top 10 Players",
        color=0xffd700
    )
    
    for i, (username, elo, wins, losses) in enumerate(top_players, 1):
        win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
        embed.add_field(
            name=f"{i}. {username}",
            value=f"ELO: {elo}\nW/L: {wins}/{losses} ({win_rate:.1f}%)",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Show your ELO profile")
async def profile(interaction: discord.Interaction):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    
    c.execute("SELECT elo, wins, losses FROM players WHERE user_id=?", (interaction.user.id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await interaction.response.send_message("You're not registered! Use `/register` first.", ephemeral=True)
        return
    
    elo, wins, losses = result
    win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
    
    # Get current level
    current_level = 0
    for level, data in ELO_LEVELS.items():
        if elo >= data["min_elo"]:
            current_level = level
    
    embed = discord.Embed(
        title=f"{interaction.user}'s Profile",
        color=0x3498db
    )
    
    embed.add_field(name="ELO", value=str(elo), inline=True)
    embed.add_field(name="Level", value=f"Level {current_level}", inline=True)
    embed.add_field(name="Wins", value=str(wins), inline=True)
    embed.add_field(name="Losses", value=str(losses), inline=True)
    embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%", inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="queue", description="Show the matchmaking queue")
async def show_queue(interaction: discord.Interaction):
    view = QueueSelectView(bot)
    await interaction.response.send_message("Select queue type:", view=view, ephemeral=True)

@bot.tree.command(name="force_start", description="Force start a match (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def force_start(interaction: discord.Interaction, queue_type: str):
    if queue_type not in QUEUE_TYPES:
        await interaction.response.send_message(
            f"Invalid queue type! Available types: {', '.join(QUEUE_TYPES.keys())}",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(f"Force starting {queue_type} match...", ephemeral=True)
    view = MatchmakingView(bot, queue_type)
    await view.start_match(interaction)

@bot.tree.command(name="reset_elo", description="Reset a player's ELO (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_elo(interaction: discord.Interaction, user: discord.Member):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    
    c.execute(
        "UPDATE players SET elo=0, wins=0, losses=0 WHERE user_id=?",
        (user.id,)
    )
    conn.commit()
    
    # Reset role to Level 1
    if ELO_LEVELS[1]["role_id"]:
        level_1_role = interaction.guild.get_role(ELO_LEVELS[1]["role_id"])
        if level_1_role:
            # Remove all other level roles
            for level_data in ELO_LEVELS.values():
                if level_data["role_id"] and level_data["role_id"] != ELO_LEVELS[1]["role_id"]:
                    role = interaction.guild.get_role(level_data["role_id"])
                    if role and role in user.roles:
                        await user.remove_roles(role)
            
            # Add Level 1 role
            if level_1_role not in user.roles:
                await user.add_roles(level_1_role)
    
    conn.close()
    
    await interaction.response.send_message(
        f"Reset ELO for {user.mention} to 0 and set to Level 1.",
        ephemeral=True
    )

@bot.tree.command(name="set_elo", description="Set a player's ELO (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def set_elo(interaction: discord.Interaction, user: discord.Member, elo: int):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    
    c.execute(
        "UPDATE players SET elo=? WHERE user_id=?",
        (elo, user.id)
    )
    conn.commit()
    
    # Update player role based on new ELO
    for level, level_data in sorted(ELO_LEVELS.items(), key=lambda x: x[0], reverse=True):
        if elo >= level_data["min_elo"]:
            if level_data["role_id"]:
                role = interaction.guild.get_role(level_data["role_id"])
                if role:
                    # Remove all other level roles
                    for other_level_data in ELO_LEVELS.values():
                        if other_level_data["role_id"] and other_level_data["role_id"] != level_data["role_id"]:
                            other_role = interaction.guild.get_role(other_level_data["role_id"])
                            if other_role and other_role in user.roles:
                                await user.remove_roles(other_role)
                    
                    # Add new role
                    if role not in user.roles:
                        await user.add_roles(role)
                    break
    
    conn.close()
    
    await interaction.response.send_message(
        f"Set {user.mention}'s ELO to {elo} and updated their level role.",
        ephemeral=True
    )

@force_start.error
@reset_elo.error
@set_elo.error
async def admin_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have permission to use this command!", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {str(error)}", ephemeral=True)
        print(f"Command error: {error}")

# Run the bot
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    TOKEN = os.getenv('TOKEN')
    
    if not TOKEN:
        print("Error: TOKEN not found in environment variables or .env file")
    else:
        bot.run(TOKEN)
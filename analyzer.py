from dataclasses import dataclass


@dataclass
class TeamAnalysis:
    team: str
    elo: float
    form: float
    attack: float
    defense: float
    injuries: float
    home_bonus: float = 0


def calculate_score(team: TeamAnalysis):

    score = (
        team.elo * 0.35 +
        team.form * 0.25 +
        team.attack * 0.15 +
        team.defense * 0.15 +
        team.injuries * 0.10 +
        team.home_bonus
    )

    return round(score,2)
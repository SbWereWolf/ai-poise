"""Semantic progression over actual complete lines and supplied Clock observations."""
import re

from .timing import elapsed


class ProgressTracker:
    def __init__(self, profile):
        self.profile = profile
        self.regex = re.compile(profile['pattern']) if profile['mode'] == 'regex' else None
        self.completed = self.total = self.stage = self.last_observation = None
        self.max_gap = None if self.regex is None else 0.0
        self.clock_comparable = True
        self.observed = False

    def observe_line(self, line, observation):
        if self.regex is None:
            return None
        match = self.regex.search(line)
        if match is None:
            return None
        try:
            completed = int(match.group(self.profile['completed_group']))
            total_group = self.profile.get('total_group')
            total = int(match.group(total_group)) if total_group and match.group(total_group) is not None else None
            stage_group = self.profile.get('stage_group')
            stage = match.group(stage_group).strip() if stage_group and match.group(stage_group) is not None else None
        except (ValueError, IndexError):
            return None
        if completed < 0 or total is not None and (total <= 0 or completed > total):
            return None
        stage_changed = self.stage is not None and stage != self.stage
        if self.completed is not None and not stage_changed and completed <= self.completed:
            return None
        if self.total is not None and total is not None and not stage_changed and total != self.total:
            return None
        if self.last_observation is not None:
            gap = elapsed(self.last_observation, observation)
            if gap is None:
                self.clock_comparable = False
            else:
                self.max_gap = max(self.max_gap, gap)
        self.completed, self.total, self.stage = completed, total, stage
        self.last_observation = observation
        self.observed = True
        return {'completed': completed, 'total': total, 'stage': stage, 'observation': observation}

    def snapshot(self):
        return {'mode': self.profile['mode'], 'observed': self.observed,
                'completed': self.completed, 'total': self.total, 'stage': self.stage,
                'max_gap_seconds': self.max_gap}

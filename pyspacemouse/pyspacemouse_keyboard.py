
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
import os

# Try to load config from YAML file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'spacemouse_config.yaml')
_user_config = None
try:
	import yaml
	with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
		_user_config = yaml.safe_load(f)
except Exception:
	_user_config = None


# ===== User-configurable settings (from config file if present) =====

def _cfg(path, default, typ=None):
	c = _user_config
	for p in path.split('.'):
		if c is None or p not in c:
			return default
		c = c[p]
	if c is None:
		return default
	if typ is not None:
		try:
			return typ(c)
		except Exception:
			return default
	return c

# Cast to correct type for safety
INVERT_X = _cfg('invert_x', False, bool)
INVERT_Y = _cfg('invert_y', True, bool)
INVERT_Z = _cfg('invert_z', True, bool)
INVERT_YAW = _cfg('invert_yaw', True, bool)
SWAP_Y_Z = _cfg('swap_y_z', False, bool)

MOVE_PRESS_MS = _cfg('move.press_ms', 0.020, float)
MOVE_MIN_HZ = _cfg('move.min_hz', 15.0, float)
MOVE_MAX_HZ = _cfg('move.max_hz', 30.0, float)
MOVE_DEADZONE = _cfg('move.deadzone', 0.001, float)
MOVE_HOLD_THRESHOLD = _cfg('move.hold_threshold', 0.40, float)
MOVE_EMA_ALPHA = _cfg('move.ema_alpha', 0.3, float)

ZOOM_PRESS_MS = _cfg('zoom.press_ms', 0.010, float)
ZOOM_MIN_HZ = _cfg('zoom.min_hz', 8.0, float)
ZOOM_MAX_HZ = _cfg('zoom.max_hz', 18.0, float)
ZOOM_DEADZONE = _cfg('zoom.deadzone', 0.001, float)
ZOOM_HOLD_THRESHOLD = _cfg('zoom.hold_threshold', 0.5, float)
ZOOM_EMA_ALPHA = _cfg('zoom.ema_alpha', 0.3, float)

# Mode settings
MODE_TOGGLE_KEY = _cfg('mode.toggle_key', 'caps_lock')
MODE_SYNC_WITH_CAPSLOCK_LED = _cfg('mode.sync_with_capslock_led', True, bool)
MODE_START_IN_CHARACTER_MODE = _cfg('mode.start_in_character_mode', False, bool)

# Mode settings
MODE_TOGGLE_KEY = _cfg('mode.toggle_key', 'caps_lock')
MODE_SYNC_WITH_CAPSLOCK_LED = _cfg('mode.sync_with_capslock_led', True, bool)
MODE_START_IN_CHARACTER_MODE = _cfg('mode.start_in_character_mode', False, bool)
# ===== End user-configurable settings =====

# Use the library in this package
from pyspacemouse import open as sm_open, read as sm_read, close as sm_close

try:
	import pynput.keyboard as keyboard
except ImportError as e:
	raise SystemExit(
		"Missing dependency: pynput. Install it with 'pip install pynput'"
	) from e

# CapsLock LED state detection
try:
	import ctypes
	import ctypes.wintypes
	_capslock_available = True
except ImportError:
	_capslock_available = False

def get_capslock_state():
	"""Get CapsLock LED state on Windows"""
	if not _capslock_available:
		return False
	try:
		# VK_CAPITAL = 0x14 (CapsLock)
		return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
	except Exception:
		return False


def clamp(v: float, lo: float, hi: float) -> float:
	return max(lo, min(hi, v))


@dataclass
class PulseState:
	key: Any
	mode: str = "pulse"  # 'pulse' or 'hold'
	pressed: bool = False
	held: bool = False
	last_pulse_time: float = 0.0
	release_due: float = 0.0


class InterpolatedKeyController:
	"""Convert analog axis magnitudes into keyboard pulses with speed control.

	Strategy:
	- Below deadzone: ensure key is released.
	- Between deadzone..hold_threshold: generate short key pulses with a frequency
	  proportional to the axis magnitude (duty-cycle control).
	- Above hold_threshold: hold the key down continuously (smoother at high speeds).
	"""

	def __init__(
		self,
		kb: keyboard.Controller,
		press_ms: float = 0.02,
		min_hz: float = 3.0,
		max_hz: float = 25.0,
		deadzone: float = 0.05,
		hold_threshold: float = 0.9,
		ema_alpha: float = 0.25,
	) -> None:
		self.kb = kb
		self.press_ms = press_ms
		self.min_hz = min_hz
		self.max_hz = max_hz
		self.deadzone = deadzone
		self.hold_threshold = hold_threshold
		self.ema_alpha = ema_alpha
		self.states: Dict[str, PulseState] = {}
		self.filtered: Dict[str, float] = {}

	def bind(self, name: str, key: Any, mode: str = "pulse") -> None:
		if mode not in ("pulse", "hold"):
			mode = "pulse"
		self.states[name] = PulseState(key=key, mode=mode)
		self.filtered[name] = 0.0

	def _ensure_released(self, st: PulseState) -> None:
		if st.pressed or st.held:
			try:
				self.kb.release(st.key)
			except Exception:
				pass
		st.pressed = False
		st.held = False
		st.release_due = 0.0

	def _press(self, st: PulseState) -> None:
		if not st.pressed:
			try:
				self.kb.press(st.key)
			except Exception:
				pass
			st.pressed = True

	def _release(self, st: PulseState) -> None:
		if st.pressed:
			try:
				self.kb.release(st.key)
			except Exception:
				pass
			st.pressed = False

	def update(self, name: str, raw_value: float, now: float) -> None:
		if name not in self.states:
			return
		st = self.states[name]

		# EMA smoothing to reduce jitter
		prev = self.filtered.get(name, 0.0)
		val = self.ema_alpha * raw_value + (1.0 - self.ema_alpha) * prev
		self.filtered[name] = val

		mag = abs(val)

		# Release opposite direction if needed (handled by caller by not updating it)

		if mag <= self.deadzone:
			# fully released below deadzone
			if st.held:
				self._ensure_released(st)
			elif st.pressed and now >= st.release_due:
				self._release(st)
			return

		if st.mode == "hold":
			# Always continuous when above deadzone
			if not st.held:
				self._press(st)
				st.held = True
			return
		else:
			# pulse mode with high-magnitude hold
			# Hold when strong input to keep motion smooth in-game
			if mag >= self.hold_threshold:
				if not st.held:
					# switch from pulse to hold
					self._press(st)
					st.held = True
				return

			# In pulsing range: ensure we're not in hold mode
			if st.held:
				self._release(st)
				st.held = False

			# Compute pulse frequency from magnitude
			# Map [deadzone, hold_threshold] -> [min_hz, max_hz]
			span = max(1e-6, self.hold_threshold - self.deadzone)
			unit = clamp((mag - self.deadzone) / span, 0.0, 1.0)
			freq = self.min_hz + unit * (self.max_hz - self.min_hz)
			interval = 1.0 / max(1e-6, freq)

			# Start a new pulse if interval elapsed
			if now - st.last_pulse_time >= interval:
				self._press(st)
				st.release_due = now + self.press_ms
				st.last_pulse_time = now

			# End pulse if its on-time elapsed
			if st.pressed and now >= st.release_due:
				self._release(st)


def main(device: Optional[str] = None, invert_yaw: bool = True) -> None:
	print("SpaceMouse → Keyboard (interpolated) using pyspacemouse")
	print("Press Ctrl+C to exit.")

	# Open SpaceMouse via library
	if device is not None:
		dev = sm_open(set_nonblocking_loop=True, device=device)
	else:
		# let pyspacemouse auto-pick the first supported device
		dev = sm_open(set_nonblocking_loop=True)
	if dev is None:
		print("No SpaceMouse device opened.")
		return

	kb = keyboard.Controller()
	# Movement controller (translation + rotation/pitch)
	ik = InterpolatedKeyController(
		kb,
		press_ms=MOVE_PRESS_MS,
		min_hz=MOVE_MIN_HZ,
		max_hz=MOVE_MAX_HZ,
		deadzone=MOVE_DEADZONE,
		hold_threshold=MOVE_HOLD_THRESHOLD,
		ema_alpha=MOVE_EMA_ALPHA,
	)
	# Zoom controller with separate sensitivity
	zoom_ik = InterpolatedKeyController(
		kb,
		press_ms=ZOOM_PRESS_MS,
		min_hz=ZOOM_MIN_HZ,
		max_hz=ZOOM_MAX_HZ,
		deadzone=ZOOM_DEADZONE,
		hold_threshold=ZOOM_HOLD_THRESHOLD,
		ema_alpha=ZOOM_EMA_ALPHA,
	)

	# Axis-to-key mapping: load from config if present, else use defaults
	_default_axis_mapping = {
		'move_left': 'a',
		'move_right': 'd',
		'move_forward': 'w',
		'move_backward': 's',
		'zoom_in': 'page_up',
		'zoom_out': 'page_down',
		'rotate_left': 'delete',
		'rotate_right': 'end',
		'pitch_up': 'up',
		'pitch_down': 'down',
	}
	_cfg_axes = _cfg('axes', None)
	axis_mapping = {}
	for k, v in _default_axis_mapping.items():
		val = _cfg_axes.get(k, v) if _cfg_axes else v
		# Convert string names to pynput keys if needed
		axis_mapping[k] = getattr(keyboard.Key, val) if hasattr(keyboard.Key, val) else val

	# Mode detection: sync with CapsLock LED or manual toggle
	character_mode = MODE_START_IN_CHARACTER_MODE
	if MODE_SYNC_WITH_CAPSLOCK_LED and _capslock_available:
		character_mode = get_capslock_state()
	
	def get_movement_mode():
		"""Return 'hold' for character mode (BG3WASD), 'pulse' for camera mode"""
		nonlocal character_mode
		if MODE_SYNC_WITH_CAPSLOCK_LED and _capslock_available:
			character_mode = get_capslock_state()
		return "hold" if character_mode else "pulse"

	# Bind keys with initial mode
	initial_mode = get_movement_mode()
	ik.bind("move_left", axis_mapping['move_left'], mode=initial_mode)
	ik.bind("move_right", axis_mapping['move_right'], mode=initial_mode)
	ik.bind("move_forward", axis_mapping['move_forward'], mode=initial_mode)
	ik.bind("move_backward", axis_mapping['move_backward'], mode=initial_mode)
	zoom_ik.bind("zoom_in", axis_mapping['zoom_in'], mode="pulse")
	zoom_ik.bind("zoom_out", axis_mapping['zoom_out'], mode="pulse")
	# rotation (twist) and pitch: always continuous hold for smooth camera
	ik.bind("rotate_left", axis_mapping['rotate_left'], mode="hold")
	ik.bind("rotate_right", axis_mapping['rotate_right'], mode="hold")
	ik.bind("pitch_up", axis_mapping['pitch_up'], mode="hold")
	ik.bind("pitch_down", axis_mapping['pitch_down'], mode="hold")

	# Track mode changes
	last_mode = initial_mode

	# Button mapping for 15 buttons (indexes 0..14)
	# Button mapping: load from config if present, else use defaults
	_default_button_mapping = {
		0: 'b',                         # Toggle Character Panels (B)
		1: 'alt_l',                     # Show world Tooltips (Left Alt)
		2: 'ctrl_l',                    # Toggle Info (Left Ctrl)
		3: 'shift_l',                   # Show Sneak Cones / Climbing Toggle (Left Shift)
		4: 'esc',                       # Cancel / In-Game Menu (Escape)
		5: 'o',                         # Toggle Tactical Camera (O)
		6: 'tab',                       # Toggle Combat Mode / Party Overview (Tab)
		7: 'c',                         # Toggle Sneak (C)
		8: 'space',                     # End Turn / Enter Turn-based (Space)
		9: 'home',                      # Camera Center (Home)
		10: 'm',                        # Toggle Map (M)
		11: 'caps_lock',                # 
		12: 'i',                        # Toggle Inventory (I)
		13: 'l',                        # Toggle Journal (L)
		14: ['shift', 'space'],         # Leave Turn-based Mode (Shift+Space)
	}
	# Load from config if present
	_cfg_buttons = _cfg('buttons', None)
	button_mapping = {}
	for idx in range(15):
		val = None
		if _cfg_buttons and str(idx) in _cfg_buttons:
			val = _cfg_buttons[str(idx)]
		elif _cfg_buttons and idx in _cfg_buttons:
			val = _cfg_buttons[idx]
		else:
			val = _default_button_mapping[idx]
		# Convert string names to pynput keys if needed
		if isinstance(val, list):
			keys = []
			for v in val:
				k = getattr(keyboard.Key, v) if hasattr(keyboard.Key, v) else v
				keys.append(k)
			button_mapping[idx] = tuple(keys)
		else:
			button_mapping[idx] = getattr(keyboard.Key, val) if hasattr(keyboard.Key, val) else val

	# Track last button states to detect rising edges
	prev_buttons = [0] * 15

	last_dir = {
		"x": 0,
		"y": 0,
		"z": 0,
		"yaw": 0,
	}

	try:
		while True:
			state = sm_read()
			now = time.time()
			if not state:
				time.sleep(0.005)
				continue

			# Check for mode changes (CapsLock LED sync)
			current_mode = get_movement_mode()
			if current_mode != last_mode:
				# Update movement key modes when CapsLock changes
				ik.states["move_left"].mode = current_mode
				ik.states["move_right"].mode = current_mode
				ik.states["move_forward"].mode = current_mode
				ik.states["move_backward"].mode = current_mode
				# Ensure keys are released when switching modes
				for name in ["move_left", "move_right", "move_forward", "move_backward"]:
					ik._ensure_released(ik.states[name])
				last_mode = current_mode
				print(f"Mode changed to: {'Character (BG3WASD)' if current_mode == 'hold' else 'Camera'}")

			# Read raw axes
			x = state.x
			y = state.y
			z = state.z
			yaw = state.yaw
			pitch = state.pitch

			# Apply global inversion flags
			if INVERT_X:
				x = -x
			if INVERT_Y:
				y = -y
			if INVERT_Z:
				z = -z
			# Keep CLI arg for yaw inversion but OR with config flag for convenience
			if INVERT_YAW:
				yaw = -yaw

			# Optional swap of Y and Z roles (move vs zoom)
			if SWAP_Y_Z:
				y, z = z, y

			# X axis -> A/D
			if x >= 0:
				ik.update("move_right", x, now)
				# ensure opposite key is idle
				ik.update("move_left", 0.0, now)
			else:
				ik.update("move_left", -x, now)
				ik.update("move_right", 0.0, now)

			# Y axis -> W/S
			if y <= 0:
				ik.update("move_forward", -y, now)
				ik.update("move_backward", 0.0, now)
			else:
				ik.update("move_backward", y, now)
				ik.update("move_forward", 0.0, now)

			# Z -> PageUp/PageDown
			if z >= 0:
				zoom_ik.update("zoom_in", z, now)
				zoom_ik.update("zoom_out", 0.0, now)
			else:
				zoom_ik.update("zoom_out", -z, now)
				zoom_ik.update("zoom_in", 0.0, now)

			# Yaw (twist) -> rotate left/right (continuous)
			if yaw >= 0:
				ik.update("rotate_right", yaw, now)
				ik.update("rotate_left", 0.0, now)
			else:
				ik.update("rotate_left", -yaw, now)
				ik.update("rotate_right", 0.0, now)

			# Pitch (continuous)
			if pitch >= 0:
				ik.update("pitch_up", abs(pitch), now)
				ik.update("pitch_down", 0.0, now)
			else:
				ik.update("pitch_down", abs(pitch), now)
				ik.update("pitch_up", 0.0, now)

			# Buttons: fire tap on rising edge
			try:
				btns = list(getattr(state, 'buttons', []))
			except Exception:
				btns = []
			if btns:
				# ensure prev_buttons has same length
				if len(prev_buttons) != len(btns):
					prev_buttons = [0] * len(btns)
				for idx, val in enumerate(btns):
					if val and not prev_buttons[idx]:
						key = button_mapping.get(idx)
						if key is not None:
							try:
								if isinstance(key, tuple):
									# Modifier combo: press all, then release in reverse
									for k in key:
										kb.press(k)
									time.sleep(0.005)
									for k in reversed(key):
										kb.release(k)
								else:
									kb.press(key)
									time.sleep(0.005)
									kb.release(key)
							except Exception:
								pass
				prev_buttons = btns

			time.sleep(0.005)

	except KeyboardInterrupt:
		pass
	finally:
		# Ensure all keys released
		for st in list(ik.states.values()) + list(zoom_ik.states.values()):
			try:
				if st.pressed or st.held:
					kb.release(st.key)
			except Exception:
				pass
		try:
			sm_close()
		except Exception:
			pass


if __name__ == "__main__":
	main()

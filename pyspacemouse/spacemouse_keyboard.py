import hid
import time
import struct
import pynput.keyboard as keyboard

def normalize_spacemouse_value(value, max_range=350):
    """Normalize SpaceMouse values to [-1, 1] range"""
    return max(-1, min(1, value / max_range))

def main():
    print("SpaceMouse to Keyboard Bridge for Baldur's Gate 3")
    print("Scanning for 3Dconnexion devices...")
    
    # Find all 3Dconnexion Universal Receiver devices
    devices = hid.enumerate(0x256F, 0xC652)
    
    if not devices:
        print("No 3Dconnexion Universal Receiver found!")
        return
    
    print(f"Found {len(devices)} 3Dconnexion device interfaces")
    
    # Find the SpaceMouse interface (usage 0x0008, usage_page 0x0001)
    target_device = None
    for device in devices:
        if device['usage'] == 0x0008 and device['usage_page'] == 0x0001:
            target_device = device
            break
    
    if not target_device:
        # If none found, try the first device
        target_device = devices[0]
    
    print(f"Connecting to SpaceMouse...")
    
    try:
        # Open the SpaceMouse device
        h = hid.device()
        h.open_path(target_device['path'])
        
        print(f"Successfully connected!")
        print(f"Manufacturer: {h.get_manufacturer_string()}")
        print(f"Product: {h.get_product_string()}")
        
        # Set non-blocking mode
        h.set_nonblocking(1)
        
        # Create keyboard controller
        kb = keyboard.Controller()
        
        print("\nSpaceMouse to Keyboard mapping for Baldur's Gate 3:")
        print("- Translation X: A/D (Camera Left/Right)")
        print("- Translation Y: W/S (Camera Forward/Backward)")
        print("- Translation Z: Page Up/Page Down (Camera Zoom)")
        print("- Rotation X (Pitch): Up/Down arrows")
        print("- Rotation Z (Puck Twist): Delete/End (Camera Rotate)")
        print("- Rotation Y (Yaw): [Available for other functions]")
        print("- SpaceMouse buttons -> Various keyboard shortcuts")
        print("\nPress Ctrl+C to exit")
        
        last_motion_time = 0
        
        # Track currently pressed keys to avoid repeated presses
        pressed_keys = set()
        
        # Movement threshold to avoid jitter
        threshold = 0.00
        
        while True:
            # Try to read data from SpaceMouse
            data = h.read(64)
            
            if data:
                current_time = time.time()
                
                if data[0] == 0x01 and len(data) >= 13:  # 6DOF motion report
                    # Parse translation data
                    x = struct.unpack('<h', bytes([data[1], data[2]]))[0]
                    y = struct.unpack('<h', bytes([data[3], data[4]]))[0]
                    z = struct.unpack('<h', bytes([data[5], data[6]]))[0]
                    
                    # Parse rotation data
                    rx = struct.unpack('<h', bytes([data[7], data[8]]))[0]  # Pitch
                    ry = struct.unpack('<h', bytes([data[9], data[10]]))[0]  # Yaw
                    rz = struct.unpack('<h', bytes([data[11], data[12]]))[0]  # Roll
                    
                    # Normalize values
                    norm_x = normalize_spacemouse_value(x)
                    norm_y = normalize_spacemouse_value(y)
                    norm_z = normalize_spacemouse_value(z)
                    norm_rx = normalize_spacemouse_value(rx)
                    norm_ry = normalize_spacemouse_value(ry)
                    norm_rz = -normalize_spacemouse_value(rz)
                    
                    # Determine which keys should be pressed
                    keys_should_be_pressed = set()
                    
                    # Translation X (Left/Right movement)
                    if norm_x > threshold:
                        keys_should_be_pressed.add('d')  # Camera Right
                    elif norm_x < -threshold:
                        keys_should_be_pressed.add('a')  # Camera Left
                    
                    # Translation Y (Forward/Backward movement)
                    if norm_y > threshold:
                        keys_should_be_pressed.add('s')  # Camera Backward
                    elif norm_y < -threshold:
                        keys_should_be_pressed.add('w')  # Camera Forward
                    
                    # Translation Z (Zoom)
                    if norm_z > threshold:
                        keys_should_be_pressed.add(keyboard.Key.page_up)  # Camera Zoom In
                    elif norm_z < -threshold:
                        keys_should_be_pressed.add(keyboard.Key.page_down)  # Camera Zoom Out

                    # # Rotation X (Pitch - unmodded BG3 does not support pitch camera movement)
                    # if norm_rx > threshold:
                    #     keys_should_be_pressed.add(keyboard.Key.up)  # Pitch up
                    # elif norm_rx < -threshold:
                    #     keys_should_be_pressed.add(keyboard.Key.down)  # Pitch down
                    
                    # Rotation Z (Puck Twist - Camera rotate left/right)
                    if norm_rz > threshold:
                        keys_should_be_pressed.add(keyboard.Key.end)  # Camera Rotate Right
                    elif norm_rz < -threshold:
                        keys_should_be_pressed.add(keyboard.Key.delete)  # Camera Rotate Left
                    
                    # Only change key states when necessary
                    # Release keys that should no longer be pressed
                    keys_to_release = pressed_keys - keys_should_be_pressed
                    for key in keys_to_release:
                        kb.release(key)
                        pressed_keys.remove(key)
                    
                    # Press keys that should be pressed but aren't yet
                    keys_to_press = keys_should_be_pressed - pressed_keys
                    for key in keys_to_press:
                        kb.press(key)
                        pressed_keys.add(key)
                    
                    # # Print throttled motion data with key states
                    # if current_time - last_motion_time > 0.2 and any([abs(x) > 50, abs(y) > 50, abs(z) > 50, abs(rx) > 50, abs(ry) > 50, abs(rz) > 50]):
                    #     active_keys = [str(k) for k in keys_should_be_pressed]
                    #     print(f"Motion: T=({x:4d}, {y:4d}, {z:4d}) R=({rx:4d}, {ry:4d}, {rz:4d}) Keys: {active_keys}")
                    #     last_motion_time = current_time
                
                elif data[0] == 0x03 and len(data) >= 5:  # Button report
                    # Parse button data according to Universal Receiver specification
                    button_specs = [
                        (1, 0),  # MENU - byte 1, bit 0
                        (3, 7),  # ALT - byte 3, bit 7
                        (4, 1),  # CTRL - byte 4, bit 1
                        (4, 0),  # SHIFT - byte 4, bit 0
                        (3, 6),  # ESC - byte 3, bit 6
                        (2, 4),  # 1 - byte 2, bit 4
                        (2, 5),  # 2 - byte 2, bit 5
                        (2, 6),  # 3 - byte 2, bit 6
                        (2, 7),  # 4 - byte 2, bit 7
                        (2, 0),  # ROLL CLOCKWISE - byte 2, bit 0
                        (1, 2),  # TOP - byte 1, bit 2
                        (4, 2),  # ROTATION - byte 4, bit 2
                        (1, 5),  # FRONT - byte 1, bit 5
                        (1, 4),  # REAR - byte 1, bit 4
                        (1, 1),  # FIT - byte 1, bit 1
                    ]
                    
                    # Map SpaceMouse buttons to useful Baldur's Gate 3 shortcuts
                    button_mapping = {
                        0: keyboard.Key.esc,        # MENU -> Game Menu (Escape)
                        1: keyboard.Key.alt_l,      # ALT -> Show world Tooltips
                        2: keyboard.Key.ctrl_l,     # CTRL -> Toggle Info
                        3: keyboard.Key.shift_l,    # SHIFT -> Show Sneak Cones / Climbing Toggle
                        4: keyboard.Key.esc,        # ESC -> Cancel Action
                        5: 'o',                     # 1 -> Toggle Tactical Camera
                        6: keyboard.Key.tab,        # 2 -> Toggle Combat Mode
                        7: 'c',                     # 3 -> Toggle Sneak
                        8: keyboard.Key.space,      # 4 -> End Turn / Enter Turn-based Mode
                        9: keyboard.Key.home,       # ROLL CLOCKWISE -> Camera Center
                        10: 'm',                    # TOP -> Toggle Map
                        11: keyboard.Key.f10,       # ROTATION -> Toggle Presentation mode
                        12: 'i',                    # FRONT -> Toggle Inventory
                        13: 'l',                    # REAR -> Toggle Journal
                        14: 'n',                    # FIT -> Toggle Character Sheet
                    }
                    
                    # Check each button according to the Universal Receiver specification
                    pressed_buttons = []
                    any_button_pressed = False
                    
                    for button_index, (byte_pos, bit_pos) in enumerate(button_specs):
                        if len(data) > byte_pos:
                            mask = 1 << bit_pos
                            if (data[byte_pos] & mask) != 0:
                                pressed_buttons.append(str(button_index))
                                any_button_pressed = True
                                
                                # Press corresponding keyboard key
                                if button_index in button_mapping:
                                    key = button_mapping[button_index]
                                    kb.press(key)
                                    time.sleep(0.005)  # Small delay
                                    kb.release(key)
                    
                    if any_button_pressed:
                        print(f"Buttons pressed: {', '.join(pressed_buttons)}")
            
            time.sleep(0.005)  # Delay to prevent high CPU usage
            
    except KeyboardInterrupt:
        print("\nExiting...")
        # Release any remaining pressed keys
        for key in pressed_keys:
            try:
                kb.release(key)
            except:
                pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        try:
            h.close()
            # Release any remaining pressed keys
            for key in pressed_keys:
                try:
                    kb.release(key)
                except:
                    pass
        except:
            pass

if __name__ == "__main__":
    main()

import argparse
import json
import os
import sys
import logging
import re
import shutil
import zipfile

# Try to import python_jsonschema_objects
try:
    import python_jsonschema_objects as pjs
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    import matplotlib.patches as patches
    import yaml
except ImportError:
    print(
        "Error: 'python_jsonschema_objects', 'matplotlib', and 'PyYAML' are required. "
        "Please install them via pip: pip install python_jsonschema_objects matplotlib PyYAML"
    )
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def load_json_file(filepath):
    """Helper to load a JSON file safely."""
    if not os.path.exists(filepath):
        logger.warning(f"File not found: {filepath}")
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {e}")
        return None

def normalize_schema(schema_dict):
    """
    Normalizes JSON schema for python_jsonschema_objects.
    Replaces '$defs' with 'definitions' and updates references.
    """
    schema_str = json.dumps(schema_dict)
    # Replace $defs with definitions
    if '"$defs"' in schema_str:
        logger.info("Normalizing schema: replacing '$defs' with 'definitions' for compatibility.")
        schema_str = schema_str.replace('"$defs"', '"definitions"')
        schema_str = schema_str.replace('"#/$defs/', '"#/definitions/')
    
    return json.loads(schema_str)

def sanitize_filename(name):
    """Removes invalid characters from a string to make it a valid filename."""
    name = name.replace(' ', '_')
    # Remove characters that are invalid in filenames on most OSes
    return re.sub(r'[<>:"/\\|?*]', '', name)

class EkahauProject:
    def __init__(self, path):
        self.path = path
        self.is_zip = zipfile.is_zipfile(path)
        self.zip_ref = None
        if self.is_zip:
            self.zip_ref = zipfile.ZipFile(path, 'r')
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.zip_ref:
            self.zip_ref.close()

    def get_json(self, filename):
        try:
            if self.is_zip:
                try:
                    with self.zip_ref.open(filename) as f:
                        return json.load(f)
                except KeyError:
                    return None
            else:
                full_path = os.path.join(self.path, filename)
                if os.path.exists(full_path):
                    with open(full_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {filename}: {e}")
            return None

    def save_image(self, image_id, dest_path):
        filename = f"image-{image_id}"
        if self.is_zip:
            try:
                with self.zip_ref.open(filename) as source, open(dest_path, 'wb') as target:
                    shutil.copyfileobj(source, target)
                return True
            except KeyError:
                return False
        else:
            src = os.path.join(self.path, filename)
            if os.path.exists(src):
                shutil.copy(src, dest_path)
                return True
            return False

def get_ekahau_data(project):
    """Reads relevant Ekahau JSON files from the project."""
    data = {}
    files_to_read = [
        'accessPoints.json', 'floorPlans.json', 'projectConfiguration.json',
        'simulatedRadios.json', 'project.json', 'walls.json', 'wallTypes.json',
        'wallSegments.json', 'wallPoints.json',
        'attenuationAreaTypes.json', 'attenuationAreas.json'
    ]
    
    for filename in files_to_read:
        key = filename.replace('.json', '')
        content = project.get_json(filename)
        if content:
            data[key] = content
            
    return data

def get_default_schema_path():
    """
    Determines the default schema path from config.yaml or environment variable.
    """
    # 1. Check config.yaml in the script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.yaml')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                if config and 'schema_path' in config:
                    return config['schema_path']
        except Exception as e:
            logger.warning(f"Failed to read config.yaml: {e}")

    # 2. Check Environment Variable
    return os.environ.get('OI_SCHEMA_PATH')

def main():
    default_schema = get_default_schema_path()

    parser = argparse.ArgumentParser(description="Convert Ekahau Data to OpenIntent Schema")
    parser.add_argument("input", help="Path to the Ekahau project file (.esx) or extracted directory")
    parser.add_argument("-o", "--output-dir", default=".", help="Path to the output directory (default: current directory)")
    parser.add_argument("--schema", default=default_schema, required=(default_schema is None), help="Path to the oi-wifi.schema.json file, if environment variable or config.yaml not found.")
    
    args = parser.parse_args()

    output_dir = os.path.abspath(args.output_dir)
    
    if not os.path.exists(args.input):
        logger.error(f"Input path not found: {args.input}")
        sys.exit(1)

    # 1. Load and Prepare Schema
    logger.info(f"Loading schema from {args.schema}")
    raw_schema = load_json_file(args.schema)
    if not raw_schema:
        sys.exit(1)

    normalized_schema = normalize_schema(raw_schema)

    # 2. Build Classes
    logger.info("Building Python classes from schema...")
    try:
        builder = pjs.ObjectBuilder(normalized_schema)
        ns = builder.build_classes()
    except Exception as e:
        logger.error(f"Failed to build classes from schema: {e}")
        sys.exit(1)

    # 3. Load Ekahau Data & 4. Convert Data
    logger.info(f"Reading Ekahau data from {args.input}")
    
    with EkahauProject(args.input) as project:
        ekahau_data = get_ekahau_data(project)
        
        if 'floorPlans' not in ekahau_data:
            logger.error("No floorPlans.json found in input directory. Cannot proceed.")
            sys.exit(1)

        # Determine output filename from project.json
        project_name = "output"
        if 'project' in ekahau_data and ekahau_data['project'].get('project'):
            project_name = ekahau_data['project']['project'].get('name', 'output')

        sanitized_project_name = sanitize_filename(project_name)
        
        # Create directory structure
        project_dir = os.path.join(output_dir, sanitized_project_name)
        images_dir = os.path.join(project_dir, "images")
        placement_dir = os.path.join(project_dir, "images_placement")
        
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(placement_dir, exist_ok=True)
        
        output_file = os.path.join(project_dir, f"{sanitized_project_name}.json")

        # Map FloorPlan ID to its data for AP association
        floor_id_map = {} # id -> {name, metersPerUnit, cropMinX, cropMinY, image_path, scale_x, scale_y}
        oi_floorplans = []
        
        # Process Floorplans
        logger.info("Processing Floorplans...")
        for fp in ekahau_data.get('floorPlans', {}).get('floorPlans', []):
            fp_name = fp.get('name', 'Unknown Floor')
            fp_id = fp.get('id')
            meters_per_unit = fp.get('metersPerUnit', 0.0)
            if meters_per_unit == 0.0:
                logger.warning(f"Floor plan '{fp_name}' has metersPerUnit set to 0. Coordinate conversion will be incorrect.")

            crop_min_x = fp.get('cropMinX', 0.0)
            crop_min_y = fp.get('cropMinY', 0.0)

            scale_x = 1.0
            scale_y = 1.0

            dest_image_path = None

            # Handle image copy and renaming
            if fp.get('bitmapImageId', None):
                bitmap_id = fp.get('bitmapImageId', None)
            elif fp.get('imageId', None):
                bitmap_id = fp.get('imageId', None)
            if bitmap_id:
                sanitized_name = sanitize_filename(fp_name)
                dest_image_path = os.path.join(images_dir, f"{sanitized_name}.jpeg")
                
                if project.save_image(bitmap_id, dest_image_path):
                    logger.info(f"Copied floor plan image for '{fp_name}' to '{dest_image_path}'")
                        
                    # Calculate scaling factor if image resolution differs from JSON
                    try:
                        img = mpimg.imread(dest_image_path)
                        real_height, real_width = img.shape[:2]
                        json_width = fp.get('width', 0)
                        json_height = fp.get('height', 0)
                        
                        if json_width > 0 and real_width > 0:
                            scale_x = real_width / json_width
                        if json_height > 0 and real_height > 0:
                            scale_y = real_height / json_height
                            
                        if scale_x != 1.0 or scale_y != 1.0:
                            logger.info(f"Floor '{fp_name}': Scaling coordinates by x={scale_x:.2f}, y={scale_y:.2f} (JSON: {json_width}x{json_height} -> Image: {real_width}x{real_height})")
                    except Exception as e:
                        logger.warning(f"Could not determine image dimensions for scaling: {e}")
                else:
                    logger.warning(f"Image file not found for floor '{fp_name}' (id: image-{bitmap_id})")
                    dest_image_path = None
            
            if fp_id:
                floor_id_map[fp_id] = {
                    'name': fp_name,
                    'metersPerUnit': meters_per_unit,
                    'image_path': dest_image_path,
                    'cropMinX': crop_min_x,
                    'cropMinY': crop_min_y,
                    'scale_x': scale_x,
                    'scale_y': scale_y
                }
                
            # Create Dimension object
            # Ekahau dimensions width/height are in pixels
            width_px = fp.get('width', 0)
            height_px = fp.get('height', 0)
            
            dim_px = ns.Dimension(
                length=float(height_px),
                width=float(width_px),
                unit="pixels",
                height=0.0
            )
            
            dim_m = ns.Dimension(
                length=height_px * meters_per_unit if meters_per_unit else 0.0,
                width=width_px * meters_per_unit if meters_per_unit else 0.0,
                unit="meters",
                height=3.0 # Default ceiling height if unknown
            )
            
            dim_ft = ns.Dimension(
                length=(height_px * meters_per_unit * 3.28084) if meters_per_unit else 0.0,
                width=(width_px * meters_per_unit * 3.28084) if meters_per_unit else 0.0,
                unit="feet",
                height=3.0 * 3.28084
            )
            
            # Create Floorplan object
            oi_fp = ns.Floorplan(
                name=fp_name,
                vendor_id=fp_id,
                dimensions=[dim_px, dim_m, dim_ft],
                rotation=0.0 # Default
            )
            
            # Optional: Add map_uri if imageId exists (placeholder logic)
            if 'imageId' in fp:
                oi_fp.map_uri = f"image:{fp['imageId']}"
            # Set map_uri to the filename if image was processed
            if dest_image_path:
                oi_fp.map_uri = f"file://images/{os.path.basename(dest_image_path)}"

            oi_floorplans.append(oi_fp)

    # Process Walls, Wall Segments, and Wall Points
    oi_wall_materials = []
    wall_type_map = {}
    
    wall_types_source = ekahau_data.get('wallTypes') or ekahau_data.get('walls')
    
    if wall_types_source:
        logger.info("Processing Wall Types...")
        for wall_type in wall_types_source.get('wallTypes', []):
            try:
                # Extract attenuation from propagationProperties
                attenuation = 0.0
                props = wall_type.get('propagationProperties', [])
                for prop in props:
                    # Prefer 5GHz
                    if prop.get('band') == 'FIVE':
                        attenuation = prop.get('attenuationFactor', 0.0)
                        break
                
                # Fallback if 0 or not found (though 0 is valid for air/cabling)
                if attenuation == 0.0 and props:
                     # Check if we have other bands with non-zero attenuation
                     for prop in props:
                         if prop.get('attenuationFactor', 0.0) > 0:
                             attenuation = prop.get('attenuationFactor')
                             break

                material = ns.Material(
                    name=wall_type.get('name'),
                    thickness_m=wall_type.get('thickness', 0.0),
                    display_color=wall_type.get('color'),
                    rf_properties=ns.RfProperties(
                        attenuation_per_m=attenuation
                    ),
                    bottom_height=wall_type.get('lowerEdge', 0.0),
                    top_height=wall_type.get('upperEdge', 3.0)
                )
                oi_wall_materials.append(material)
                wall_type_map[wall_type['id']] = material
            except Exception as e:
                logger.warning(f"Could not process wall type {wall_type.get('id')}: {e}")

    wall_point_map = {}
    if 'wallPoints' in ekahau_data:
        logger.info("Processing Wall Points...")
        for point in ekahau_data['wallPoints'].get('wallPoints', []):
            wall_point_map[point['id']] = point.get('location')

    floor_wall_segments_map = {} # floorId -> list of wall segments
    if 'wallSegments' in ekahau_data and wall_point_map:
        logger.info("Processing Wall Segments...")
        for segment in ekahau_data['wallSegments'].get('wallSegments', []):
            p1_id = segment.get('point1Id')
            p2_id = segment.get('point2Id')
            
            loc1 = wall_point_map.get(p1_id)
            loc2 = wall_point_map.get(p2_id)

            if not loc1 or not loc2:
                logger.warning(f"Skipping wall segment {segment.get('id')} due to missing points.")
                continue

            floor_id = loc1.get('floorPlanId')
            if floor_id != loc2.get('floorPlanId'):
                logger.warning(f"Wall segment {segment.get('id')} spans across different floors. Skipping.")
                continue

            floor_info = floor_id_map.get(floor_id)
            if not floor_info:
                logger.warning(f"Wall segment {segment.get('id')} on unknown floor {floor_id}. Skipping.")
                continue
            
            # Get conversion factors
            mpu = floor_info.get('metersPerUnit') or 0.0
                
            crop_min_x = floor_info.get('cropMinX', 0.0)
            crop_min_y = floor_info.get('cropMinY', 0.0)
            scale_x = floor_info.get('scale_x', 1.0)
            scale_y = floor_info.get('scale_y', 1.0)
            
            # Get coordinates in pixels (Ekahau coords are in map units/pixels)
            x1_px = (loc1.get('coord', {}).get('x', 0.0) - crop_min_x) * scale_x
            y1_px = (loc1.get('coord', {}).get('y', 0.0) - crop_min_y) * scale_y
            x2_px = (loc2.get('coord', {}).get('x', 0.0) - crop_min_x) * scale_x
            y2_px = (loc2.get('coord', {}).get('y', 0.0) - crop_min_y) * scale_y
            
            wall_type_id = segment.get('wallTypeId')
            material = wall_type_map.get(wall_type_id)
            wall_type_name = material.name if material else "Unknown Wall"

            oi_segment = ns.WallSegment(
                wall_type=wall_type_name,
                start_point=ns.WallPoint(x=x1_px, y=y1_px),
                end_point=ns.WallPoint(x=x2_px, y=y2_px)
            )
            
            if floor_id not in floor_wall_segments_map:
                floor_wall_segments_map[floor_id] = []
            floor_wall_segments_map[floor_id].append(oi_segment)

    # Assign wall segments to floorplans
    if floor_wall_segments_map:
        for fp in oi_floorplans:
            segments = floor_wall_segments_map.get(fp.vendor_id)
            if segments:
                fp.wall_segments = segments

    # Process Attenuation Area Materials
    oi_area_materials = []
    attenuation_type_map = {}
    
    if 'attenuationAreaTypes' in ekahau_data:
        logger.info("Processing Attenuation Area Types...")
        for area_type in ekahau_data['attenuationAreaTypes'].get('attenuationAreaTypes', []):
            try:
                # Extract attenuation
                attenuation = 0.0
                props = area_type.get('propagationProperties', [])
                for prop in props:
                    if prop.get('band') == 'FIVE':
                        attenuation = prop.get('attenuationFactor', 0.0)
                        break
                if attenuation == 0.0 and props:
                     for prop in props:
                         if prop.get('attenuationFactor', 0.0) > 0:
                             attenuation = prop.get('attenuationFactor')
                             break
                
                material = ns.Material(
                    name=area_type.get('name'),
                    display_color=area_type.get('color'),
                    rf_properties=ns.RfProperties(
                        attenuation_flat=attenuation
                    ),
                    bottom_height=area_type.get('lowerEdge', 0.0),
                    top_height=area_type.get('upperEdge', 0.0)
                )
                oi_area_materials.append(material)
                attenuation_type_map[area_type['id']] = material
            except Exception as e:
                logger.warning(f"Could not process attenuation area type {area_type.get('id')}: {e}")

    # Process Attenuation Areas
    floor_attenuation_areas_map = {}
    if 'attenuationAreas' in ekahau_data:
        logger.info("Processing Attenuation Areas...")
        for area in ekahau_data['attenuationAreas'].get('attenuationAreas', []):
            floor_id = area.get('floorPlanId')
            floor_info = floor_id_map.get(floor_id)
            if not floor_info:
                continue
            
            crop_min_x = floor_info.get('cropMinX', 0.0)
            crop_min_y = floor_info.get('cropMinY', 0.0)
            scale_x = floor_info.get('scale_x', 1.0)
            scale_y = floor_info.get('scale_y', 1.0)

            points = area.get('area', [])
            if len(points) < 3:
                continue
            
            coords = []
            for pt in points:
                x = (pt.get('x', 0.0) - crop_min_x) * scale_x
                y = (pt.get('y', 0.0) - crop_min_y) * scale_y
                
                coord = ns.Coordinate(
                    coordinate_xyz=ns.CoordinateXyz(
                        x=x, y=y, z=0.0, unit="pixels"
                    )
                )
                coords.append(coord)
            
            type_id = area.get('typeId') or area.get('attenuationAreaTypeId')
            material = attenuation_type_map.get(type_id)
            
            oi_area = ns.AttenuationArea(
                area=ns.Area(coordinates=coords),
                area_material=material if material else ns.Material(name="Unknown")
            )
            
            if floor_id not in floor_attenuation_areas_map:
                floor_attenuation_areas_map[floor_id] = []
            floor_attenuation_areas_map[floor_id].append(oi_area)

    if floor_attenuation_areas_map:
        for fp in oi_floorplans:
            fp.attenuation_areas = floor_attenuation_areas_map.get(fp.vendor_id)

    # Pre-process Radios
    ap_radios_map = {}
    if 'simulatedRadios' in ekahau_data:
        for radio in ekahau_data['simulatedRadios'].get('simulatedRadios', []):
            ap_id = radio.get('accessPointId')
            if ap_id:
                if ap_id not in ap_radios_map:
                    ap_radios_map[ap_id] = []
                ap_radios_map[ap_id].append(radio)

    # Process Access Points
    oi_accesspoints = []
    logger.info("Processing Access Points...")
    
    if 'accessPoints' in ekahau_data:
        for ap in ekahau_data['accessPoints'].get('accessPoints', []):
            ap_name = ap.get('name', 'Unknown AP')
            ap_model = ap.get('model', 'Unknown Model')
            ap_vendor = ap.get('vendor', 'Unknown Vendor')
            ap_id = ap.get('id')
            
            # Determine Floorplan Name
            location = ap.get('location', {})
            floor_id = location.get('floorPlanId')
            floor_info = floor_id_map.get(floor_id)
            
            if floor_info:
                floor_name = floor_info['name']
                mpu = floor_info.get('metersPerUnit') or 0.0
                crop_min_x = floor_info.get('cropMinX', 0.0)
                crop_min_y = floor_info.get('cropMinY', 0.0)
                scale_x = floor_info.get('scale_x', 1.0)
                scale_y = floor_info.get('scale_y', 1.0)
            else:
                logger.warning(f"AP {ap_name} has unknown floorPlanId: {floor_id}. Skipping assignment.")
                continue

            # Create Coordinates
            # Ekahau location x,y are usually relative to the floorplan origin
            coord_data = location.get('coord')
            if coord_data:
                coord_x = coord_data.get('x') or 0
                coord_y = coord_data.get('y') or 0
            else:
                coord_x = location.get('x') or 0
                coord_y = location.get('y') or 0
            
            # Determine Z from radios (antennaHeight)
            z_height = 0.0
            ap_radios = ap_radios_map.get(ap_id, [])
            for r in ap_radios:
                h = r.get('antennaHeight', 0.0)
                if h > z_height:
                    z_height = h
            
            # Create Coordinates in Pixels, Meters, Feet
            
            # Pixels
            coord_px = ns.Coordinate(coordinate_xyz=ns.CoordinateXyz(
                x=(coord_x - crop_min_x) * scale_x,
                y=(coord_y - crop_min_y) * scale_y,
                z=(z_height / mpu * scale_x) if mpu > 0 else 0.0,
                unit="pixels"
            ))
            
            # Meters
            coord_m = ns.Coordinate(coordinate_xyz=ns.CoordinateXyz(
                x=(coord_x - crop_min_x) * mpu,
                y=(coord_y - crop_min_y) * mpu,
                z=z_height,
                unit="meters"
            ))
            
            # Feet
            ft_per_m = 3.28084
            coord_ft = ns.Coordinate(coordinate_xyz=ns.CoordinateXyz(
                x=((coord_x - crop_min_x) * mpu * ft_per_m),
                y=((coord_y - crop_min_y) * mpu * ft_per_m),
                z=z_height * ft_per_m,
                unit="feet"
            ))

            # Create Radios (Simulated/Measured)
            dot11_radios = []
            
            if ap_radios:
                for r in ap_radios:
                    # Skip Bluetooth
                    if r.get('radioTechnology') == 'BLUETOOTH':
                        continue
                    
                    # Map Channel
                    channels = r.get('channel', [])
                    primary_channel = channels[0] if channels else 0
                    
                    # Map Band
                    band = "FREQ_5GHZ" # Default
                    if r.get('frequencyBand') == 'TWO':
                        band = "FREQ_2.4GHZ"
                    elif r.get('frequencyBand') == 'FIVE':
                        band = "FREQ_5GHZ"
                    elif r.get('frequencyBand') == 'SIX':
                        band = "FREQ_6GHZ"
                    
                    # Map Transmit Power
                    tx_power = int(round(r.get('transmitPower', 0)))
                    
                    radio_obj = ns.Dot11Radio(
                        id=r.get('accessPointIndex', 0) + 1,
                        radio_function="CLIENT_ACCESS" if r.get('enabled', True) else "DISABLED",
                        band=band,
                        channel=primary_channel,
                        transmit_power=tx_power
                    )
                    dot11_radios.append(radio_obj)

            # Fallback if no radios found
            if not dot11_radios:
                dot11_radios.append(ns.Dot11Radio(
                    id=1,
                    radio_function="CLIENT_ACCESS",
                    band="FREQ_5GHZ",
                    channel=0,
                    transmit_power=0
                ))

            # Create AccessPoint object
            oi_ap = ns.Accesspoint(
                name=ap_name,
                floorplan_name=floor_name,
                manufacturer=ap_vendor,
                model=ap_model,
                coordinates=[coord_px, coord_m, coord_ft],
                dot11_radios=dot11_radios
            )
            
            # Optional fields
            if 'id' in ap:
                oi_ap.asset_tag = ap['id'] # Using Ekahau ID as asset tag for reference
            
            oi_accesspoints.append(oi_ap)

    # 5. Create Root Object
    logger.info("Creating OpenIntent Root Object...")
    try:
        oi_root = ns.OiWifi(
            openintent_version="2.0.0",
            accesspoints=oi_accesspoints,
            floorplans=oi_floorplans,
            wall_materials=oi_wall_materials if oi_wall_materials else None,
            area_materials=oi_area_materials if oi_area_materials else None
        )
    except Exception as e:
        logger.error(f"Validation failed during object creation: {e}")
        sys.exit(1)

    # 6. Serialize and Save
    logger.info(f"Writing output to {output_file}")
    
    json_str = oi_root.serialize()
    json_data = json.loads(json_str)
    with open(output_file, 'w') as f:
        json.dump(json_data, f, indent=2)
    
    # 7. Draw APs and Walls on Images
    logger.info("Drawing APs and walls on floor plan images...")

    # Create a map of wall material names to their colors
    wall_material_color_map = {}
    if oi_root.wall_materials:
        for material in oi_root.wall_materials:
            if material.name and material.display_color:
                wall_material_color_map[str(material.name)] = str(material.display_color)

    # Group AP coordinates by floorplan name using the OpenIntent object
    floor_ap_map = {} # floor_name -> list of (x, y)
    
    if oi_root.accesspoints:
        for ap in oi_root.accesspoints:
            # Find pixel coordinates
            px_x, px_y = None, None
            if ap.coordinates:
                for coord in ap.coordinates:
                    # Check unit. pjs might return a wrapper, so cast to str
                    if coord.coordinate_xyz and str(coord.coordinate_xyz.unit) == 'pixels':
                        px_x = coord.coordinate_xyz.x
                        px_y = coord.coordinate_xyz.y
                        break
            
            if px_x is not None and px_y is not None:
                fp_name = str(ap.floorplan_name)
                if fp_name not in floor_ap_map:
                    floor_ap_map[fp_name] = []
                floor_ap_map[fp_name].append((px_x, px_y))

    if oi_root.floorplans:
        for fp in oi_root.floorplans:
            if not fp.map_uri:
                continue
            
            image_filename = str(fp.map_uri)
            if image_filename.startswith("file://"):
                image_filename = image_filename[7:]
            image_path = os.path.join(project_dir, image_filename)
            
            if not os.path.exists(image_path):
                logger.warning(f"Skipping drawing on non-existent image: {image_path}")
                continue
            
            try:
                img = mpimg.imread(image_path)
                
                ap_coords = floor_ap_map.get(str(fp.name), [])
                wall_segments = fp.wall_segments if fp.wall_segments else []
                attenuation_areas = fp.attenuation_areas if fp.attenuation_areas else []
                if not ap_coords and not wall_segments and not attenuation_areas:
                    continue

                height, width = img.shape[:2]

                # Create a figure and axes to match the image dimensions
                # Calculate DPI to maintain consistent marker size relative to image dimensions
                # Markers are defined in points (1/72 inch). By scaling DPI with image width,
                # we ensure markers appear at a consistent scale regardless of image resolution.
                target_width_inches = 20.0
                dpi = width / target_width_inches
                fig = plt.figure(figsize=(target_width_inches, height / dpi), dpi=dpi)
                ax = fig.add_axes([0, 0, 1, 1])
                ax.imshow(img)
                
                if attenuation_areas:
                    logger.info(f"Adding {len(attenuation_areas)} attenuation areas to {os.path.basename(image_path)}")
                    for area in attenuation_areas:
                        pts = []
                        if area.area and area.area.coordinates:
                            for c in area.area.coordinates:
                                if c.coordinate_xyz:
                                    pts.append((c.coordinate_xyz.x, c.coordinate_xyz.y))
                        
                        if pts:
                            color = '#808080'
                            if area.area_material and area.area_material.display_color:
                                color = str(area.area_material.display_color)
                            
                            poly = patches.Polygon(pts, closed=True, fill=True, facecolor=color, edgecolor=color, alpha=0.3, zorder=4)
                            ax.add_patch(poly)

                if wall_segments:
                    logger.info(f"Adding {len(wall_segments)} wall segments to {os.path.basename(image_path)}")
                    for segment in wall_segments:
                        start = segment.start_point
                        end = segment.end_point
                        color = wall_material_color_map.get(str(segment.wall_type), '#808080')
                        ax.plot([start.x, end.x], [start.y, end.y], color=color, linewidth=2, zorder=5)

                if ap_coords:
                    logger.info(f"Adding {len(ap_coords)} APs to {os.path.basename(image_path)}")
                    x_coords, y_coords = zip(*ap_coords)
                    ax.plot(x_coords, y_coords, 'go', markersize=5, markeredgecolor='black', markeredgewidth=0.5, zorder=10)

                ax.axis('off')
                
                placement_image_path = os.path.join(placement_dir, os.path.basename(image_filename))
                plt.savefig(placement_image_path, dpi=dpi)
                plt.close(fig)
            except Exception as e:
                logger.error(f"Failed to draw on image {image_path}: {e}")

    logger.info("Conversion complete.")

if __name__ == "__main__":
    main()
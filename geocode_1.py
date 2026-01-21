from qgis.core import (QgsProject, QgsGeometry, QgsWkbTypes, QgsVectorLayer, 
                       QgsFeature, QgsPointXY, QgsField, QgsTask, QgsMessageLog, 
                       Qgis, QgsFeatureRequest, QgsSpatialIndex, QgsRectangle)
from PyQt5.QtCore import QVariant
from collections import deque, defaultdict
import re

class ProcessInstructionsTask(QgsTask):
    def __init__(self, streets_layer_name, input_layer_name, relaxed_mode=False):
        super().__init__("Geocode Cross Streets", QgsTask.CanCancel)
        
        self.relaxed_mode = relaxed_mode
        
        streets_layers = QgsProject.instance().mapLayersByName(streets_layer_name)
        if not streets_layers:
            print(f"Streets layer '{streets_layer_name}' not found.")
            return
        self.streets_layer = streets_layers[0]

        layers = QgsProject.instance().mapLayersByName(input_layer_name)
        if not layers:
            raise ValueError(f"Layer '{input_layer_name}' not found")
        self.input_layer = layers[0]

        crs = self.input_layer.crs().authid()
        self.out_layer = QgsVectorLayer(f"MultiLineString?crs={crs}", "selected_streets", "memory")
        self.out_layer.dataProvider().addAttributes(self.input_layer.fields())
        self.out_layer.updateFields()
        QgsProject.instance().addMapLayer(self.out_layer)
        self.out_pr = self.out_layer.dataProvider()
        
        # Create failure analysis layer
        self.fail_layer = QgsVectorLayer(f"NoGeometry?crs={crs}", "geocoding_failures", "memory")
        fail_fields = [
            QgsField("location", QVariant.String),
            QgsField("main_street", QVariant.String),
            QgsField("cross_a", QVariant.String),
            QgsField("cross_b", QVariant.String),
            QgsField("borough", QVariant.String),
            QgsField("failure_reason", QVariant.String),
            QgsField("main_found", QVariant.String),
            QgsField("cross_a_found", QVariant.String),
            QgsField("cross_b_found", QVariant.String),
            QgsField("suggestion", QVariant.String),
        ]
        self.fail_layer.dataProvider().addAttributes(fail_fields)
        self.fail_layer.updateFields()
        QgsProject.instance().addMapLayer(self.fail_layer)
        
        # Failure tracking
        self.failure_stats = defaultdict(int)
        
        # Pre-build indexes and caches
        print("Building spatial index and caches...")
        self.build_caches()
        print("Caches built successfully")

    def deduplicate_location(self, text):
        """Remove redundant repetitions, address ranges, and extra whitespace from location text.
        
        Handles cases like:
        'MYRTLE AVENUE between LEWIS AVENUE and NOSTRAND AVENUE,  MYRTLE AVENUE between LEWIS AVENUE and NOSTRAND AVENUE'
        '581-645 MACDONOUGH STREET between RALPH AVENUE and HOWARD AVENUE'
        '683A MYRTLE AVENUE between BEDFORD AVENUE and SPENCER STREET'
        'Marcy Avenue Plaza: Marcy Avenue Plaza Marcy Avenue between Fulton Street and Macdonough Street'
        '2010 FULTON STREET between SARATOGA AVENUE and HULL ST'
        Returns the first unique occurrence with address ranges and prefixes removed.
        """
        # Strip whitespace
        text = text.strip()
        
        # Remove prefix patterns like "Name: Name " or "Name Name " before the actual location
        # Pattern: word(s) followed by optional colon and then same word(s) repeated
        text = re.sub(r'^([A-Z][A-Za-z\s]*?):\s*\1\s+', '', text, flags=re.IGNORECASE)
        # Handle case without colon: "Marcy Avenue Plaza Marcy Avenue Plaza "
        text = re.sub(r'^([A-Z][A-Za-z\s]+?)\s+\1\s+', '', text, flags=re.IGNORECASE)
        
        # Remove everything before and including "between" if there's an address range
        # Pattern: anything followed by "between" (case-insensitive)
        match = re.search(r'(between\s+.*)', text, re.IGNORECASE)
        if match:
            # Extract from "between" onwards
            between_part = match.group(1)
            # Get everything before "between"
            before_between = text[:match.start()].strip()
            
            # Remove address numbers and letters from the beginning
            # Pattern: remove leading digits, letters that are part of address (like A, B in 683A), dashes, etc.
            # Keep only the actual street name
            cleaned = re.sub(r'^[\d\-\./]*\s*[A-Z]?\s+', '', before_between)
            cleaned = cleaned.strip()
            
            if cleaned:
                text = f"{cleaned} {between_part}"
            else:
                # If we can't extract a street name, just use the between part
                text = between_part
        else:
            # If no "between" found, just remove all leading numbers, dashes, and address letters
            text = re.sub(r'^[\d\s\-./]*[A-Z]?\s+', '', text)
        
        # Normalize extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Standardize cross street order (alphabetically)
        text = self.standardize_cross_street_order(text)
        
        # Split by comma and process each part
        parts = [part.strip() for part in text.split(',')]
        
        # If all parts are identical, return the first one
        if len(parts) > 1 and len(set(parts)) == 1:
            return parts[0]
        
        # If parts are similar but not exact (handle extra spaces), return first
        if len(parts) > 1:
            # Normalize whitespace in all parts
            normalized_parts = [re.sub(r'\s+', ' ', part) for part in parts]
            if len(set(normalized_parts)) == 1:
                return normalized_parts[0]
        
        # Return the full text if no redundancy detected
        return text

    def standardize_cross_street_order(self, text):
        """Standardize the order of cross streets alphabetically.
        
        Converts:
        'BAINBRIDGE STREET between HOWARD AVENUE and SARATOGA AVENUE'
        To:
        'BAINBRIDGE STREET between HOWARD AVENUE and SARATOGA AVENUE' (already in order)
        
        And converts:
        'BAINBRIDGE STREET between SARATOGA AVENUE and HOWARD AVENUE'
        To:
        'BAINBRIDGE STREET between HOWARD AVENUE and SARATOGA AVENUE' (reordered)
        """
        # Match pattern: "Street between Cross_A and Cross_B"
        match = re.match(r'(.*?)\s+between\s+(.*?)\s+and\s+(.*)', text, re.IGNORECASE)
        if not match:
            return text
        
        main_street = match.group(1).strip()
        cross_a = match.group(2).strip()
        cross_b = match.group(3).strip()
        
        # Sort cross streets alphabetically
        cross_streets = sorted([cross_a, cross_b])
        
        # Reconstruct with sorted order
        return f"{main_street} between {cross_streets[0]} and {cross_streets[1]}"

    def build_caches(self):
        """Pre-build all spatial indexes and name lookups once"""
        self.spatial_index = QgsSpatialIndex()
        self.feature_cache = {}
        self.name_borough_index = defaultdict(lambda: defaultdict(list))
        self.endpoint_map = defaultdict(list)
        self.geometry_cache = {}
        
        # Track all unique street names per borough for fuzzy matching
        self.all_street_names = defaultdict(set)
        
        for feat in self.streets_layer.getFeatures():
            fid = feat.id()
            geom = feat.geometry()
            
            # Cache feature and geometry
            self.feature_cache[fid] = feat
            self.geometry_cache[fid] = geom
            
            # Add to spatial index
            self.spatial_index.addFeature(feat)
            
            # Index by name and borough
            name = feat['stname_lab']
            borough = feat['boroughcod']
            if name and borough:
                self.name_borough_index[borough][name].append(fid)
                self.all_street_names[borough].add(name)
            
            # Build endpoint map
            lines = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            for line in lines:
                if len(line) >= 2:
                    start = self.round_point(line[0])
                    end = self.round_point(line[-1])
                    self.endpoint_map[start].append(fid)
                    self.endpoint_map[end].append(fid)

    @staticmethod
    def round_point(pt, precision=6):
        """Round point coordinates for consistent matching"""
        return (round(pt.x(), precision), round(pt.y(), precision))

    def fuzzy_match_street(self, target_name, borough_code):
        """Enhanced fuzzy matching with more variations"""
        available_names = self.all_street_names[borough_code]
        if not available_names:
            return None, []
        
        # Exact match first
        if target_name in available_names:
            return target_name, []
        
        variations = []
        
        # MAC/MC variations (critical for your data)
        variations.extend([
            target_name.replace('MACDONOUGH', 'MAC DONOUGH'),
            target_name.replace('MAC DONOUGH', 'MACDONOUGH'),
            target_name.replace('MCDONOUGH', 'MAC DONOUGH'),
            target_name.replace('MAC DONOUGH', 'MCDONOUGH'),
            target_name.replace('MACDOUGAL', 'MAC DOUGAL'),
            target_name.replace('MAC DOUGAL', 'MACDOUGAL'),
        ])
        
        # Common street name variations
        name_mappings = {
            'REID AVE': ['RALPH AVE'],
            'RALPH AVE': ['REID AVE'],
            'SUMNER AVE': ['SUMPTER AVE', 'MARCUS GARVEY BLVD', 'MARCUS GARVEY AVE'],
            'SUMPTER AVE': ['SUMNER AVE'],
            'MARCUS GARVEY BLVD': ['SUMNER AVE', 'MARCUS GARVEY AVE'],
            'MARCUS GARVEY AVE': ['SUMNER AVE', 'MARCUS GARVEY BLVD'],
            'DO THE RIGHT THING WAY': ['STUYVESANT AVE'],
            'STUYVESANT AVE': ['DO THE RIGHT THING WAY'],
            'MARCY AVE': ['REV GARDNER C TAYLOR BLVD', 'REV DR GARDNER C TAYLOR BLVD', 'REV DR G C TAYLOR BLVD'],
            'REV GARDNER C TAYLOR BLVD': ['MARCY AVE'],
            'REV DR GARDNER C TAYLOR BLVD': ['MARCY AVE'],
            'REV DR G C TAYLOR BLVD': ['MARCY AVE'],
            'MALCOM X BLVD': ['MALCOLM X BLVD'],
            'MALCOLM X BLVD': ['MALCOM X BLVD'],
            'KOSCIUSKO ST': ['KOSCIUSZKO ST'],
            'KOSCIUSZKO ST': ['KOSCIUSKO ST'],
        }
        
        if target_name in name_mappings:
            variations.extend(name_mappings[target_name])
        
        # AVE/BLVD/ST variations
        for old, new in [(' AVE', ' BLVD'), (' BLVD', ' AVE'), (' AVE', ' ST'), (' ST', ' AVE')]:
            if target_name.endswith(old):
                variations.append(target_name[:-len(old)] + new)
        
        # Check all variations
        for var in variations:
            if var in available_names:
                return var, []
        
        # Find similar names (substring match)
        similar = []
        target_words = set(target_name.split())
        for name in available_names:
            name_words = set(name.split())
            if len(target_words & name_words) >= min(2, len(target_words)):
                similar.append(name)
        
        return None, sorted(similar)[:3]

    def log_failure(self, input_feat, main_street, cross_a, cross_b, 
                   borough_code, reason, main_fids, cross_a_fids, cross_b_fids):
        """Log detailed failure information"""
        borough_name = input_feat['Event Borough']
        location = input_feat["Event Location"]
        
        suggestions = []
        if not main_fids:
            match, similar = self.fuzzy_match_street(main_street, borough_code)
            if similar:
                suggestions.append(f"Main: Try {', '.join(similar[:3])}")
        
        if not cross_a_fids:
            match, similar = self.fuzzy_match_street(cross_a, borough_code)
            if similar:
                suggestions.append(f"Cross A: Try {', '.join(similar[:3])}")
        
        if not cross_b_fids:
            match, similar = self.fuzzy_match_street(cross_b, borough_code)
            if similar:
                suggestions.append(f"Cross B: Try {', '.join(similar[:3])}")
        
        suggestion_text = " | ".join(suggestions) if suggestions else ""
        
        fail_feat = QgsFeature(self.fail_layer.fields())
        fail_feat.setAttributes([
            location,
            main_street,
            cross_a,
            cross_b,
            borough_name,
            reason,
            "YES" if main_fids else "NO",
            "YES" if cross_a_fids else "NO",
            "YES" if cross_b_fids else "NO",
            suggestion_text
        ])
        self.fail_layer.dataProvider().addFeature(fail_feat)
        self.failure_stats[reason] += 1

    def run(self):
        success_count = 0
        total = self.input_layer.featureCount()
        
        # Dictionary to track cleaned locations for updating the input layer
        cleaned_locations = {}

        for i, input_feat in enumerate(self.input_layer.getFeatures()):
            if self.isCanceled():
                return False

            feat_id = input_feat.id()
            text = input_feat["Event Location"]
            # Remove redundant repetitions in location text
            cleaned_text = self.deduplicate_location(text)
            # Store the cleaned location for later update
            cleaned_locations[feat_id] = cleaned_text
            
            match = re.match(r"(.*?)\s+between\s+(.*?)\s+and\s+(.*)", cleaned_text, re.IGNORECASE)
            if not match:
                print(f"Could not parse instruction: {cleaned_text}")
                fail_feat = QgsFeature(self.fail_layer.fields())
                fail_feat.setAttributes([cleaned_text, "", "", "", 
                                        input_feat['Event Borough'], 
                                        "Parse error", "N/A", "N/A", "N/A", ""])
                self.fail_layer.dataProvider().addFeature(fail_feat)
                self.failure_stats["Parse error"] += 1
                continue

            main_street = self.fix_name(match.group(1).strip())
            cross_a = self.fix_name(match.group(2).strip())
            cross_b = self.fix_name(match.group(3).strip())
            borough_code = self.get_borough_code(input_feat['Event Borough'])
            
            # Handle DEAD END case
            if cross_b == "DEAD END" or cross_b == "DEADEND":
                selected_ids = self.select_to_dead_end(main_street, cross_a, borough_code)
                if selected_ids:
                    success_count += 1
                    geoms = [self.geometry_cache[fid] for fid in selected_ids]
                    merged_geom = QgsGeometry.unaryUnion(geoms)
                    out_feat = QgsFeature(self.out_layer.fields())
                    out_feat.setGeometry(merged_geom)
                    # Update attributes with cleaned location (no numbers)
                    attrs = list(input_feat.attributes())
                    location_idx = input_feat.fieldNameIndex("Event Location")
                    if location_idx >= 0:
                        attrs[location_idx] = re.sub(r'\d+', '', cleaned_text).strip()
                    out_feat.setAttributes(attrs)
                    self.out_pr.addFeature(out_feat)
                else:
                    self.log_failure(input_feat, main_street, cross_a, "DEAD END", 
                                   borough_code, "Could not find street to dead end", 
                                   [], [], [])
                if (i + 1) % 100 == 0:
                    print(f"Processed {i + 1}/{total} ({success_count} successful, {i + 1 - success_count} failed)")
                self.setProgress((i + 1) / total * 100)
                continue

            # Try fuzzy matching for each street
            main_match, _ = self.fuzzy_match_street(main_street, borough_code)
            if main_match:
                main_street = main_match
                
            cross_a_match, _ = self.fuzzy_match_street(cross_a, borough_code)
            if cross_a_match:
                cross_a = cross_a_match
                
            cross_b_match, _ = self.fuzzy_match_street(cross_b, borough_code)
            if cross_b_match:
                cross_b = cross_b_match

            main_fids, cross_a_fids, cross_b_fids, selected_ids, reason = \
                self.select_between_with_diagnostics(main_street, cross_a, cross_b, borough_code)

            if not selected_ids:
                self.log_failure(input_feat, main_street, cross_a, cross_b, 
                               borough_code, reason, main_fids, cross_a_fids, cross_b_fids)
                continue

            success_count += 1
                
            geoms = [self.geometry_cache[fid] for fid in selected_ids]
            merged_geom = QgsGeometry.unaryUnion(geoms)

            out_feat = QgsFeature(self.out_layer.fields())
            out_feat.setGeometry(merged_geom)
            # Update attributes with cleaned location (no numbers)
            attrs = list(input_feat.attributes())
            location_idx = input_feat.fieldNameIndex("Event Location")
            if location_idx >= 0:
                attrs[location_idx] = re.sub(r'\d+', '', cleaned_text).strip()
            out_feat.setAttributes(attrs)
            self.out_pr.addFeature(out_feat)

            if (i + 1) % 100 == 0:
                print(f"Processed {i + 1}/{total} ({success_count} successful, {i + 1 - success_count} failed)")
            self.setProgress((i + 1) / total * 100)

        # Update the input layer with cleaned locations
        self.update_location_column(cleaned_locations)

        # Add Location_Count column to output layer
        self.add_location_count_column()

        # Print summary statistics
        print("\n" + "="*60)
        print("GEOCODING SUMMARY")
        print("="*60)
        print(f"Total records: {total}")
        print(f"Successes: {success_count} ({success_count/total*100:.1f}%)")
        print(f"Failures: {total - success_count} ({(total-success_count)/total*100:.1f}%)")
        print("\nFailure breakdown:")
        for reason, count in sorted(self.failure_stats.items(), key=lambda x: -x[1]):
            print(f"  • {reason}: {count} ({count/total*100:.1f}%)")
        print("="*60)
        print(f"\n✓ Check 'geocoding_failures' layer for detailed analysis")
        print(f"✓ Check 'selected_streets' layer for successful matches")
        print(f"✓ Location column in input layer has been standardized")
        print(f"✓ Location_Count column added to output layer\n")

        return True

    def finished(self, result):
        QgsMessageLog.logMessage("Task Completed", "Street Processor", Qgis.Info)

    def update_location_column(self, cleaned_locations):
        """Update the Event Location column in the input layer with standardized values"""
        try:
            # Start edit session on the input layer
            self.input_layer.startEditing()
            
            # Get the index of the Event Location field
            location_field_idx = self.input_layer.fields().indexFromName("Event Location")
            
            if location_field_idx < 0:
                print("Warning: 'Event Location' field not found in input layer")
                return
            
            # Update each feature with its cleaned location
            for feat_id, cleaned_text in cleaned_locations.items():
                self.input_layer.changeAttributeValue(feat_id, location_field_idx, cleaned_text)
            
            # Commit the changes
            self.input_layer.commitChanges()
            print(f"✓ Updated {len(cleaned_locations)} location entries in input layer")
            
        except Exception as e:
            print(f"Error updating location column: {e}")
            self.input_layer.rollBack()

    def add_location_count_column(self):
        """Add a Location_Count column to the output layer showing occurrence count"""
        try:
            # Add a new field if it doesn't already exist
            field_name = "Location_Count"
            existing_fields = [f.name() for f in self.out_layer.fields()]
            
            if field_name not in existing_fields:
                self.out_pr.addAttributes([QgsField(field_name, QVariant.Int)])
                self.out_layer.updateFields()

            # Build dictionary of counts per Location
            location_counts = defaultdict(int)
            
            for f in self.out_layer.getFeatures():
                loc = f.attribute("Event Location")
                if loc:
                    location_counts[loc] += 1

            # Start editing and assign counts
            self.out_layer.startEditing()
            count_idx = self.out_layer.fields().indexFromName(field_name)

            if count_idx < 0:
                print("Warning: 'Location_Count' field not found")
                return

            for f in self.out_layer.getFeatures():
                loc = f.attribute("Event Location")
                count = location_counts.get(loc, 0)
                self.out_layer.changeAttributeValue(f.id(), count_idx, count)

            self.out_layer.commitChanges()
            print(f"✓ Added '{field_name}' column with counts for {len(location_counts)} unique locations")

        except Exception as e:
            print(f"Error adding Location_Count column: {e}")
            self.out_layer.rollBack()

    def get_features_by_name(self, name, borough_code):
        """Fast lookup using pre-built index"""
        return self.name_borough_index[borough_code].get(name, [])

    def find_intersections_fast(self, main_fids, cross_fids, tolerance=0.00001):
        """Find intersection points using spatial index with increased tolerance"""
        intersection_pts = set()
        
        for cross_fid in cross_fids:
            cross_geom = self.geometry_cache[cross_fid]
            bbox = cross_geom.boundingBox()
            bbox.grow(tolerance)
            
            candidate_fids = self.spatial_index.intersects(bbox)
            
            for main_fid in candidate_fids:
                if main_fid not in main_fids:
                    continue
                    
                main_geom = self.geometry_cache[main_fid]
                if main_geom.intersects(cross_geom.buffer(tolerance, 1)):
                    inter = main_geom.intersection(cross_geom.buffer(tolerance, 1))
                    
                    if inter.type() == QgsWkbTypes.PointGeometry:
                        pt = inter.asPoint()
                        intersection_pts.add(self.round_point(pt))
                    elif inter.type() == QgsWkbTypes.LineGeometry:
                        line_pts = inter.asPolyline() if not inter.isMultipart() else inter.asMultiPolyline()[0]
                        for pt in line_pts:
                            intersection_pts.add(self.round_point(pt))
        
        return intersection_pts

    def get_segment_endpoints(self, fid):
        """Get rounded endpoints for a segment"""
        geom = self.geometry_cache[fid]
        lines = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
        endpoints = []
        for line in lines:
            if len(line) >= 2:
                endpoints.append(self.round_point(line[0]))
                endpoints.append(self.round_point(line[-1]))
        return endpoints

    def find_segments_at_points(self, fids, points):
        """Find which segments touch the given points"""
        result = set()
        for fid in fids:
            endpoints = self.get_segment_endpoints(fid)
            if any(pt in points for pt in endpoints):
                result.add(fid)
        return result

    def bfs_between(self, start_fids, end_fids, all_main_fids):
        """BFS to find path between two sets of segments"""
        visited = set()
        selected = set()
        queue = deque(start_fids)
        
        while queue:
            fid = queue.popleft()
            if fid in visited:
                continue
            visited.add(fid)
            
            if fid in end_fids and fid not in start_fids:
                continue
                
            selected.add(fid)
            
            for endpoint in self.get_segment_endpoints(fid):
                for neighbor_fid in self.endpoint_map[endpoint]:
                    if (neighbor_fid in all_main_fids and 
                        neighbor_fid not in visited and 
                        neighbor_fid not in start_fids):
                        queue.append(neighbor_fid)
        
        return selected

    def select_between_with_diagnostics(self, main_street, cross_a, cross_b, 
                                       borough_code, tolerance=0.00001):
        """Selection with detailed diagnostics and progressive tolerance"""
        
        main_fids = self.get_features_by_name(main_street, borough_code)
        cross_a_fids = self.get_features_by_name(cross_a, borough_code)
        cross_b_fids = self.get_features_by_name(cross_b, borough_code)

        if not main_fids:
            return main_fids, cross_a_fids, cross_b_fids, [], "Main street not found"
        if not cross_a_fids:
            return main_fids, cross_a_fids, cross_b_fids, [], "Cross street A not found"
        if not cross_b_fids:
            return main_fids, cross_a_fids, cross_b_fids, [], "Cross street B not found"

        # Try progressively larger tolerances
        for tol in [tolerance, 0.0001, 0.001]:
            cross_a_pts = self.find_intersections_fast(main_fids, cross_a_fids, tol)
            cross_b_pts = self.find_intersections_fast(main_fids, cross_b_fids, tol)
            
            if cross_a_pts and cross_b_pts:
                cross_a_segments = self.find_segments_at_points(main_fids, cross_a_pts)
                cross_b_segments = self.find_segments_at_points(main_fids, cross_b_pts)

                main_fids_set = set(main_fids)
                a_selected = self.bfs_between(cross_a_segments, cross_b_segments, main_fids_set)
                b_selected = self.bfs_between(cross_b_segments, cross_a_segments, main_fids_set)
                
                selected = list(a_selected & b_selected)
                
                if selected:
                    return main_fids, cross_a_fids, cross_b_fids, selected, "Success"
        
        # If standard approach fails, try relaxed mode (increased tolerance)
        cross_a_pts = self.find_intersections_fast(main_fids, cross_a_fids, 0.01)
        cross_b_pts = self.find_intersections_fast(main_fids, cross_b_fids, 0.01)
        
        if cross_a_pts and cross_b_pts:
            # Try relaxed BFS with larger search area
            cross_a_segments = self.find_segments_at_points(main_fids, cross_a_pts)
            cross_b_segments = self.find_segments_at_points(main_fids, cross_b_pts)
            
            if cross_a_segments and cross_b_segments:
                relaxed_selected = self.select_between_relaxed(main_fids, cross_a_segments, 
                                                               cross_b_segments, cross_a_pts, cross_b_pts)
                if relaxed_selected:
                    return main_fids, cross_a_fids, cross_b_fids, relaxed_selected, "Success (relaxed)"
        
        # Determine specific failure reason
        cross_a_pts = self.find_intersections_fast(main_fids, cross_a_fids, 0.01)
        cross_b_pts = self.find_intersections_fast(main_fids, cross_b_fids, 0.01)
        
        if not cross_a_pts:
            return main_fids, cross_a_fids, cross_b_fids, [], "Main street doesn't intersect cross street A"
        if not cross_b_pts:
            return main_fids, cross_a_fids, cross_b_fids, [], "Main street doesn't intersect cross street B"
        
        return main_fids, cross_a_fids, cross_b_fids, [], "No continuous path between cross streets"

    def select_between_relaxed(self, main_fids, cross_a_segments, cross_b_segments, 
                               cross_a_pts, cross_b_pts):
        """Relaxed selection for streets with topology issues"""
        if not cross_a_pts or not cross_b_pts:
            return []
        
        all_cross_pts = list(cross_a_pts) + list(cross_b_pts)
        
        min_x = min(pt[0] for pt in all_cross_pts)
        max_x = max(pt[0] for pt in all_cross_pts)
        min_y = min(pt[1] for pt in all_cross_pts)
        max_y = max(pt[1] for pt in all_cross_pts)
        
        x_range = max_x - min_x
        y_range = max_y - min_y
        # More generous buffer for disconnected segments
        buffer = max(x_range, y_range) * 0.2
        
        selected = []
        
        # Select segments that fall within the bounding box and are near the cross streets
        cross_a_nearby = set()
        cross_b_nearby = set()
        
        for fid in main_fids:
            geom = self.geometry_cache[fid]
            bbox = geom.boundingBox()
            
            # Check if segment is in the general area between cross streets
            if not (bbox.xMaximum() < min_x - buffer or 
                    bbox.xMinimum() > max_x + buffer or
                    bbox.yMaximum() < min_y - buffer or 
                    bbox.yMinimum() > max_y + buffer):
                
                # Track if close to cross street A or B
                if fid in cross_a_segments:
                    cross_a_nearby.add(fid)
                if fid in cross_b_segments:
                    cross_b_nearby.add(fid)
                    
                selected.append(fid)
        
        # If we found segments near both cross streets, return all intermediate segments
        if cross_a_nearby and cross_b_nearby:
            return selected
        
        # Fallback: just return segments within the bounding box
        return selected

    def select_to_dead_end(self, main_street, cross_street, borough_code, tolerance=0.00001):
        """Select all segments of main_street from cross_street to the dead end"""
        main_fids = self.get_features_by_name(main_street, borough_code)
        cross_fids = self.get_features_by_name(cross_street, borough_code)
        
        if not main_fids or not cross_fids:
            return []
        
        cross_pts = self.find_intersections_fast(main_fids, cross_fids, tolerance)
        if not cross_pts:
            return []
        
        cross_segments = self.find_segments_at_points(main_fids, cross_pts)
        if not cross_segments:
            return []
        
        visited = set()
        selected = set()
        queue = deque(cross_segments)
        main_fids_set = set(main_fids)
        
        while queue:
            fid = queue.popleft()
            if fid in visited:
                continue
            visited.add(fid)
            selected.add(fid)
            
            for endpoint in self.get_segment_endpoints(fid):
                for neighbor_fid in self.endpoint_map[endpoint]:
                    if neighbor_fid in main_fids_set and neighbor_fid not in visited:
                        queue.append(neighbor_fid)
        
        return list(selected)

    def fix_name(self, name):
        """Enhanced name normalization"""
        name = name.upper().strip()
        
        # Remove leading numbers and extra characters
        name = re.sub(r'^\d+[-\s]+', '', name)
        name = name.replace('.', ' ')
        name = re.sub(r'\s+', ' ', name).strip()
        
        # Prefix replacements
        prefixes = {
            'AVENUE ': 'AVE ',
            'SAINT ': 'ST ',
            'REVEREND ': 'REV ',
            'DOCTOR ': 'DR ',
            'EAST ': 'E ',
            'WEST ': 'W ',
            'NORTH ': 'N ',
            'SOUTH ': 'S ',
        }
        
        for full, abbr in prefixes.items():
            if name.startswith(full):
                name = abbr + name[len(full):]
                break
        
        # Suffix replacements
        suffixes = {
            ' AVENUE': ' AVE',
            ' BOULEVARD': ' BLVD',
            ' COURT': ' CT',
            ' STREET': ' ST',
            ' PLACE': ' PL',
            ' ROAD': ' RD',
            ' PARKWAY': ' PKWY',
            ' TERRACE': ' TER',
            ' DRIVE': ' DR',
        }
        
        for full, abbr in suffixes.items():
            if name.endswith(full):
                name = name[:-len(full)] + abbr
                break
        
        return name

    def get_borough_code(self, borough):
        """Convert borough name to code"""
        boroughs = {
            'Manhattan': '1',
            'Bronx': '2',
            'Brooklyn': '3',
            'Queens': '4',
            'Staten Island': '5',
        }
        return boroughs.get(borough, '1')


# Usage:
task = ProcessInstructionsTask('streets_layer_name', 'input_layer_name', relaxed_mode=True)
QgsApplication.taskManager().addTask(task)

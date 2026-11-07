// Exact depth for the options sketch: ray-cast the user’s visible meshes from the capture camera over the sketch's screen
// region (the Rhino z-buffer here is +-1.5 m noisy at 25 m). Writes depth_rc.bin (float32, h x w at 1/STEP of the
// capture, 0 = nothing) + depth_rc.json, in the same format as cm_core.depth_from_meta. Runs once on Idle.
// TEMPLATE: before pasting into execute_rhinocommon_csharp_code, replace MI with numpy.linalg.inv(world_to_screen)
// flattened row-major, and set CAP, W/H, the screen region X0..Y1 (the sketch's projected bbox), loc and cdir (meta
// camera location/direction) and box (camera plus the sketched area). Poll depth_rc.log for "done".
var CAP = @"<capture folder>\analysis\";
int W = 2118, H = 1196, STEP = 2;
int X0 = 240, X1 = 1980, Y0 = 430, Y1 = 830;
double[] Mi = { MI };
var loc = new Point3d(-70457.0145, 28754.4399, 23077.5602);
var cdir = new Vector3d(0.7922, -0.1641, -0.5878); cdir.Unitize();
var box = new BoundingBox(-71000, 14000, 5500, -44000, 35000, 24000);
var log = CAP + "depth_rc.log";
System.IO.File.WriteAllText(log, "queued\n");
Func<double, double, double, Point3d> un = (x, y, z) => {
    double a = Mi[0]*x + Mi[1]*y + Mi[2]*z + Mi[3], b = Mi[4]*x + Mi[5]*y + Mi[6]*z + Mi[7];
    double c = Mi[8]*x + Mi[9]*y + Mi[10]*z + Mi[11], w = Mi[12]*x + Mi[13]*y + Mi[14]*z + Mi[15];
    return new Point3d(a / w, b / w, c / w);
};
EventHandler handler = null;
handler = (s, e) => {
    RhinoApp.Idle -= handler;
    var t0 = DateTime.Now;
    try {
        var es = new ObjectEnumeratorSettings(); es.VisibleFilter = true; es.HiddenObjects = false;
        es.ObjectTypeFilter = ObjectType.Brep | ObjectType.Extrusion | ObjectType.Mesh | ObjectType.SubD | ObjectType.Surface;
        var meshes = new List<Mesh>(); var boxes = new List<BoundingBox>(); int nobj = 0, nblocks = 0;
        foreach (var o in doc.Objects.GetObjectList(es)) {
            if (!doc.Layers[o.Attributes.LayerIndex].IsVisible) continue;
            var bb = o.Geometry.GetBoundingBox(true);
            if (!BoundingBox.Intersection(bb, box).IsValid) continue;
            var ms = o.GetMeshes(MeshType.Render);
            Mesh j = new Mesh();
            if (ms != null && ms.Length > 0) { foreach (var m in ms) j.Append(m); }
            else {
                Brep br = o.Geometry as Brep;
                if (br == null && o.Geometry is Extrusion) br = ((Extrusion)o.Geometry).ToBrep();
                if (br == null && o.Geometry is Surface) br = ((Surface)o.Geometry).ToBrep();
                if (br != null) { var mm = Mesh.CreateFromBrep(br, MeshingParameters.FastRenderMesh); if (mm != null) foreach (var m in mm) j.Append(m); }
                else if (o.Geometry is Mesh) j.Append((Mesh)o.Geometry);
            }
            if (j.Faces.Count == 0) continue;
            meshes.Add(j); boxes.Add(j.GetBoundingBox(true)); nobj++;
        }
        foreach (var o in doc.Objects.GetObjectList(new ObjectEnumeratorSettings { ObjectTypeFilter = ObjectType.InstanceReference, VisibleFilter = true }))
            if (BoundingBox.Intersection(o.Geometry.GetBoundingBox(true), box).IsValid) nblocks++;
        // bucket each mesh by its projected bbox on a 32 px grid
        int G = 32, gw = W / G + 1, gh = H / G + 1;
        var buckets = new List<int>[gw * gh];
        for (int i = 0; i < buckets.Length; i++) buckets[i] = new List<int>();
        var M = new Transform(1.0);
        // world_to_screen = inverse of Mi
        var Minv = new Transform(1.0);
        for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) Minv[r, c] = Mi[r * 4 + c];
        Transform W2S; Minv.TryGetInverse(out W2S);
        for (int k = 0; k < meshes.Count; k++) {
            double sx0 = 1e9, sy0 = 1e9, sx1 = -1e9, sy1 = -1e9; bool behind = false;
            foreach (var p in boxes[k].GetCorners()) {
                if ((p - loc) * cdir <= 1.0) { behind = true; break; }
                double xx = W2S[0,0]*p.X + W2S[0,1]*p.Y + W2S[0,2]*p.Z + W2S[0,3];
                double yy = W2S[1,0]*p.X + W2S[1,1]*p.Y + W2S[1,2]*p.Z + W2S[1,3];
                double ww = W2S[3,0]*p.X + W2S[3,1]*p.Y + W2S[3,2]*p.Z + W2S[3,3];
                xx /= ww; yy /= ww;
                sx0 = Math.Min(sx0, xx); sx1 = Math.Max(sx1, xx); sy0 = Math.Min(sy0, yy); sy1 = Math.Max(sy1, yy);
            }
            int gx0 = 0, gx1 = gw - 1, gy0 = 0, gy1 = gh - 1;
            if (!behind) {
                gx0 = Math.Max(0, (int)Math.Floor(sx0 / G)); gx1 = Math.Min(gw - 1, (int)Math.Floor(sx1 / G));
                gy0 = Math.Max(0, (int)Math.Floor(sy0 / G)); gy1 = Math.Min(gh - 1, (int)Math.Floor(sy1 / G));
            }
            for (int gy = gy0; gy <= gy1; gy++) for (int gx = gx0; gx <= gx1; gx++) buckets[gy * gw + gx].Add(k);
        }
        int w = W / STEP, h = H / STEP; var vals = new float[w * h]; int hits = 0, rays = 0;
        for (int gyy = Y0 / STEP; gyy < Y1 / STEP; gyy++) {
            for (int gxx = X0 / STEP; gxx < X1 / STEP; gxx++) {
                double px = gxx * STEP + STEP / 2.0, py = gyy * STEP + STEP / 2.0;
                var a = un(px, py, 0.0); var b = un(px, py, 1.0);
                var d = b - a; d.Unitize(); if (d * cdir < 0) d = -d;
                var ray = new Ray3d(loc, d); rays++;
                var line = new Line(loc, loc + d * 200000.0);
                double best = double.MaxValue;
                var bucket = buckets[((int)py / G) * gw + ((int)px / G)];
                foreach (int k in bucket) {
                    Interval iv;
                    if (!Rhino.Geometry.Intersect.Intersection.LineBox(line, boxes[k], 0.0, out iv)) continue;
                    if (iv.Min * 200000.0 > best) continue;
                    double t = Rhino.Geometry.Intersect.Intersection.MeshRay(meshes[k], ray);
                    if (t >= 0 && t < best) best = t;
                }
                if (best < double.MaxValue) { vals[gyy * w + gxx] = (float)((ray.PointAt(best) - loc) * cdir); hits++; }
            }
        }
        using (var fs = new System.IO.FileStream(CAP + "depth_rc.bin", System.IO.FileMode.Create))
        using (var bw = new System.IO.BinaryWriter(fs)) foreach (var v in vals) bw.Write(v);
        System.IO.File.WriteAllText(CAP + "depth_rc.json", "{\"w\": " + w + ", \"h\": " + h + ", \"step\": " + STEP +
            ", \"capture_size\": [" + W + ", " + H + "], \"region\": [" + X0 + ", " + Y0 + ", " + X1 + ", " + Y1 + "], \"hits\": " + hits + "}");
        System.IO.File.AppendAllText(log, "done meshes " + nobj + " blocks_skipped " + nblocks + " rays " + rays + " hits " + hits +
            " secs " + (DateTime.Now - t0).TotalSeconds.ToString("0.0") + "\n");
    } catch (Exception ex) { System.IO.File.AppendAllText(log, "ERROR " + ex + "\n"); }
};
RhinoApp.Idle += handler;
output.AppendLine("queued");

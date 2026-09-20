using System;
using System.Collections.Generic;

/// <summary>Coordinator-owned step. Ordered IDs select primary then secondary masks.</summary>
[Serializable]
public sealed class GuideStep
{
    public string guide_id;
    public int generation;
    public int step_index;
    public int total_steps;
    public int[] active_obj_ids;
    public string instruction;

    public bool IsValid
    {
        get
        {
            if (string.IsNullOrEmpty(guide_id) || generation < 0 || total_steps < 1
                || total_steps > 8 || step_index < 0 || step_index >= total_steps
                || string.IsNullOrWhiteSpace(instruction) || instruction.Length > 240
                || active_obj_ids == null || active_obj_ids.Length < 1 || active_obj_ids.Length > 3) return false;
            var seen = new HashSet<int>();
            foreach (int id in active_obj_ids)
                if (id < 1 || id > 3 || !seen.Add(id)) return false;
            return true;
        }
    }

    public float OpacityFor(int objId)
    {
        if (active_obj_ids == null) return 0f;
        int index = Array.IndexOf(active_obj_ids, objId);
        return index < 0 ? 0f : index == 0 ? 1f : 0.35f;
    }
}

[Serializable]
public sealed class GuideFinished
{
    public string guide_id;
    public int generation;
    public string reason;
    public string instruction;
}

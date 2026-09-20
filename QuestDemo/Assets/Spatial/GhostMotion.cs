using UnityEngine;

/// <summary>Surface-tangent ghost motion (rotate or slide per parent spec §8).</summary>
public class GhostMotion : MonoBehaviour
{
    public string MotionKind;
    public string Axis = "y";
    public float AngleDeg = 45f;
    public float DistanceM = 0.1f;
    public float PeriodS = 2f;

    Vector3 _baseLocalPos;
    Quaternion _baseLocalRot;

    void Start()
    {
        _baseLocalPos = transform.localPosition;
        _baseLocalRot = transform.localRotation;
    }

    void Update()
    {
        if (PeriodS <= 0f)
            return;
        float phase = (Time.time % PeriodS) / PeriodS;
        float wave = Mathf.Sin(phase * Mathf.PI * 2f);
        if (MotionKind == "rotate")
        {
            Vector3 axis = TangentAxis(Axis);
            transform.localRotation = _baseLocalRot * Quaternion.AngleAxis(AngleDeg * wave, axis);
        }
        else if (MotionKind == "slide")
        {
            Vector3 dir = TangentAxis(Axis);
            transform.localPosition = _baseLocalPos + dir * (DistanceM * 0.5f * wave);
        }
    }

    static Vector3 TangentAxis(string axis)
    {
        if (axis == "x")
            return Vector3.right;
        if (axis == "z")
            return Vector3.forward;
        return Vector3.up;
    }
}

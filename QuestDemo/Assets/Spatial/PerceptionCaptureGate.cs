/// <summary>Owns a bounded capture and fences native results after timeout/cancel.</summary>
public sealed class PerceptionCaptureGate
{
    int _sequence;
    int _ticket;
    int _nativeTicket;
    float _deadline;
    public bool Active { get; private set; }
    public bool ReadbackPending { get { return _nativeTicket != 0; } }

    public int Begin(float now)
    {
        if (Active || ReadbackPending) return 0;
        _ticket = ++_sequence;
        _deadline = now + 0.75f;
        Active = true;
        return _ticket;
    }

    public bool StartReadback(int ticket)
    {
        if (!Active || ticket != _ticket || ReadbackPending) return false;
        _nativeTicket = ticket;
        return true;
    }

    public bool AcceptResult(int ticket, float now)
    {
        if (_nativeTicket != ticket) return false;
        _nativeTicket = 0;
        if (!Active || ticket != _ticket || now >= _deadline) return false;
        Active = false;
        return true;
    }

    public bool Expire(float now)
    {
        if (!Active || now < _deadline) return false;
        Active = false;
        return true;
    }

    public void Cancel() { Active = false; }
}

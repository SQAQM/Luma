using Luma.Core.Management;

namespace Luma.Preview.Views;

public sealed partial class ViewerView
{
    private ManagementState? _management;
    private bool _favorite, _annotationWriting;
    private int _rating, _annotationGeneration;
    // At most the size of a neighbor window, retaining only changed annotation
    // fields. A response captured before a commit cannot undo that local commit.
    private readonly Dictionary<(long Id, string Identity), (bool? Favorite, int? Rating, long Sequence)> _annotationEdits = [];
    private long _annotationSequence;
    private (long Id, string Identity)? _annotationFeedbackFor;
    private const string AnnotationSavingMessage = "正在保存…";

    internal void SetManagement(ManagementState? state)
    {
        if (ReferenceEquals(_management, state)) return;
        if (_management is not null) _management.Changed -= ManagementChanged;
        _annotationGeneration++;
        _annotationWriting = false;
        _annotationEdits.Clear();
        _management = state;
        if (state is not null) state.Changed += ManagementChanged;
        UpdateReactions();
    }

    private void ManagementChanged() { UpdateReactions(); if (IsOpen) Changed?.Invoke(); }

    private void SyncAnnotations()
    {
        var current = _model?.Current;
        if (_annotationFeedbackFor is { } owner && (current?.Id != owner.Id || current?.Identity != owner.Identity))
        {
            // Only clear our own pending message. Metadata/file feedback may have
            // replaced it already and belongs to the newly displayed image.
            if (Feedback.Text == AnnotationSavingMessage) Feedback.Text = "";
            _annotationFeedbackFor = null;
        }
        _favorite = current?.Favorite ?? false;
        _rating = current?.Rating ?? 0;
        if (current is not null && _annotationEdits.TryGetValue((current.Id, current.Identity), out var edit))
        {
            // Equality is not proof of freshness: an older neighbor response may
            // match a later ABA edit. Retain committed fields for this session.
            _favorite = edit.Favorite ?? _favorite;
            _rating = edit.Rating ?? _rating;
        }
        UpdateReactions();
    }

    private void UpdateReactions() => Reactions.Bind(_favorite, _rating,
        IsOpen && _model?.Current is not null && !_annotationWriting && _management is { Writing: false, Uncertain: false }, "Viewer");

    private async void ChangeAnnotation(bool? favorite, int? rating)
    {
        PausePlayback();
        if (_disposed || !IsOpen || _annotationWriting || _management is not { Writing: false, Uncertain: false } state || _model is not { Current: { } current } model) return;
        if (favorite is null && rating is null || rating is < 0 or > 5) return;
        var args = new Dictionary<string, object?> { ["imageId"] = current.Id, ["identity"] = current.Identity };
        if (favorite is not null) args["favorite"] = favorite.Value;
        if (rating is not null) args["rating"] = rating.Value;
        int generation = _annotationGeneration;
        _annotationWriting = true; _annotationFeedbackFor = (current.Id, current.Identity); Feedback.Text = AnnotationSavingMessage; RestoreControls(); UpdateReactions(); Changed?.Invoke();
        bool saved;
        try { saved = await state.WriteAsync("image.patch", args); }
        catch (Exception)
        {
            // Keep an unexpected transport/local-receipt failure visible without
            // turning an async UI event into an application crash or retry.
            if (!_disposed && generation == _annotationGeneration && ReferenceEquals(_model, model))
            {
                _annotationWriting = false;
                if (IsOpen && model.Current?.Id == current.Id && model.Current.Identity == current.Identity)
                {
                    Feedback.Text = "状态待核对";
                    _annotationFeedbackFor = null;
                }
                UpdateReactions(); RestartIdle(); Changed?.Invoke();
            }
            return;
        }
        bool sameSession = !_disposed && ReferenceEquals(_management, state) && generation == _annotationGeneration && ReferenceEquals(_model, model);
        // A submitted write belongs to its original identity. Navigating or closing
        // cannot cancel that write, and its completion must not annotate another photo.
        if (sameSession)
        {
            _annotationWriting = false;
            if (saved && !state.Uncertain)
            {
                var key = (current.Id, current.Identity);
                _annotationEdits.TryGetValue(key, out var previous);
                _annotationEdits[key] = (favorite ?? previous.Favorite, rating ?? previous.Rating, ++_annotationSequence);
                if (_annotationEdits.Count > 9) _annotationEdits.Remove(_annotationEdits.MinBy(pair => pair.Value.Sequence).Key);
            }
            SyncAnnotations();
            if (IsOpen && model.Current?.Id == current.Id && model.Current.Identity == current.Identity)
            {
                Feedback.Text = saved && !state.Uncertain ? "已保存" : state.Feedback;
                _annotationFeedbackFor = null;
            }
            RestartIdle(); Changed?.Invoke();
        }
        if (saved && !state.Uncertain && !_disposed && ReferenceEquals(_management, state)) AnnotationChanged?.Invoke();
    }
}

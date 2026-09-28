// The one-sentence live status, floating over the map.
function StoryBar({ story, notice }) {
  return (
    <div className={`story story-${story.tone}`} role="status">
      <span className="story-dot" />
      <div>
        <p>{story.text}</p>
        {notice && <small>{notice}</small>}
      </div>
    </div>
  );
}

export default StoryBar;

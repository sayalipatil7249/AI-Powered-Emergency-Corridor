// Renders one of the SVG strings from icons.js inside React.
function SvgIcon({ svg, className = "" }) {
  return (
    <span
      className={`svg-icon ${className}`}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

export default SvgIcon;

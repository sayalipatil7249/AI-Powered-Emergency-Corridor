import SvgIcon from "../components/SvgIcon";
import ThemeToggle from "../components/ThemeToggle";
import { HOSPITAL_ICON } from "../icons";

// The same header on every admin page: logo and title on the left;
// on the right the theme switch, the page's own controls (children)
// and the way back to the live map.
function AdminHeader({ title = "Admin", subtitle = "108 ambulance control and records", children }) {
  return (
    <header className="header">
      <div className="brand">
        <SvgIcon svg={HOSPITAL_ICON} className="brand-mark" />
        <div>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
      </div>
      <div className="header-actions">
        <ThemeToggle />
        {children}
        <a className="button button-primary" href="#/">
          Live map
        </a>
      </div>
    </header>
  );
}

export default AdminHeader;

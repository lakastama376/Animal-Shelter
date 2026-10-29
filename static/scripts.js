// Navbar
const hamburgerBtn = document.getElementById('hamburgerBtn');
const closeBtn = document.getElementById('closeBtn');
const navbarRight = document.getElementById('navbarRight');
if (hamburgerBtn && navbarRight) {
    hamburgerBtn.addEventListener('click', function() {
        navbarRight.classList.add('active');
    });
}
if (closeBtn && navbarRight) {
    closeBtn.addEventListener('click', function() {
        navbarRight.classList.remove('active');
    });
}
const profileTrigger = document.querySelector('.profile-trigger');
const profileDropdownBox = document.querySelector('.profile-dropdown-box');
if (profileTrigger && profileDropdownBox) {
    profileTrigger.addEventListener('click', function(event) {
        event.stopPropagation(); 
        profileDropdownBox.classList.toggle('show');
    });
    document.addEventListener('click', function() {
        profileDropdownBox.classList.remove('show');
    });
}

// Sidebar
document.addEventListener('DOMContentLoaded', () => {
    const sidebarElement = document.getElementById('appSidebar');
    const mobileIndicator = document.getElementById('mobileIndicator');
    const mainContentArea = document.querySelector('.content') || document.querySelector('main') || document.getElementById('content-wrapper');
    if (!sidebarElement) {
        if (mainContentArea) {
            mainContentArea.classList.remove('collapsed');
            mainContentArea.style.marginLeft = '0';
        }
        if (mobileIndicator) mobileIndicator.style.display = 'none';
        return; 
    }

    if (mainContentArea) {
        mainContentArea.classList.add('has-sidebar');
    }

    function syncSidebarDimensions() {
        const viewportHeight = window.innerHeight;
        if (sidebarElement && window.innerWidth > 900) {
            sidebarElement.style.height = `${viewportHeight}px`;
        }
    }
    syncSidebarDimensions();
    window.addEventListener('resize', syncSidebarDimensions);

    const isMobileView = window.innerWidth <= 960;
    const savedSidebarState = localStorage.getItem('sidebarCollapsed');
    if (isMobileView || savedSidebarState === 'true' || savedSidebarState === null) {
        sidebarElement.classList.add('collapsed');
        if (mainContentArea) mainContentArea.classList.add('collapsed');
        if (savedSidebarState === null && !isMobileView) {
            localStorage.setItem('sidebarCollapsed', 'true');
        }
    } else if (!isMobileView && savedSidebarState === 'false') {
        sidebarElement.classList.remove('collapsed');
        if (mainContentArea) mainContentArea.classList.remove('collapsed');
    }

    function updateIconState() {
        const toggleButtons = document.querySelectorAll('#sidebarToggleBtn');
        const isCollapsed = sidebarElement.classList.contains('collapsed');

        if (!isMobileView) {
            localStorage.setItem('sidebarCollapsed', isCollapsed);
        }
        
        if (mainContentArea) {
            if (isCollapsed) {
                mainContentArea.classList.add('collapsed');
            } else {
                mainContentArea.classList.remove('collapsed');
            }
        }
        toggleButtons.forEach(btn => {
            const targetElement = btn.querySelector('i') || btn.querySelector('svg');
            if (!targetElement) return;

            if (isCollapsed) {
                targetElement.setAttribute('data-lucide', 'menu');
                if (mobileIndicator) mobileIndicator.style.opacity = "1";
            } else {
                targetElement.setAttribute('data-lucide', 'chevron-left');
                if (mobileIndicator) mobileIndicator.style.opacity = "0";
            }
        });
        if (window.lucide) {
            lucide.createIcons();
        }
    }

    if (window.lucide) {
        lucide.createIcons();
    }
    updateIconState();

    document.querySelectorAll('#sidebarToggleBtn').forEach(btn => {
        btn.addEventListener('click', () => {
            sidebarElement.classList.toggle('collapsed');
            updateIconState();
        });
    });
    if (mobileIndicator) {
        mobileIndicator.addEventListener('click', () => {
            sidebarElement.classList.remove('collapsed');
            updateIconState();
        });
    }
});
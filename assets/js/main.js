"use strict";

/* ======= Header animation ======= */   
const header = document.getElementById('header');  

window.onload=function() 
{   
    headerAnimation(); 

};

window.onresize=function() 
{   
    headerAnimation(); 

}; 

window.onscroll=function() 
{ 
    headerAnimation(); 

}; 
    

function headerAnimation () {

    var scrollTop = window.scrollY;
	
	if ( scrollTop > 100 ) {	    
	    header.classList.add('header-shrink');    
	    	    
	} else {
	    header.classList.remove('header-shrink');
	}

};

/* ===== Sponsor links jump, never glide (Marek 2026-09-21) ===== */
/* "Sponsor" buttons and nav links (#sponsor on home pages, #sponsors on event pages) used to start a smooth scroll
   while the lead form's "Email us" panel was animating open and lazy images were still arriving above: the page
   grew under the glide and it sometimes stopped half way. They now teleport: the panel opens without its
   transition, the page jumps in the same tick, and the landing is re-checked once shortly after and once on load
   unless the visitor has touched the page. Every other anchor keeps its smooth scroll. Never throws. */
(function () {
	try {
		var target = document.getElementById('sponsor') || document.getElementById('sponsors');
		if (!target) return;
		var hash = '#' + target.id;
		var touched = false;
		['wheel', 'touchmove', 'keydown', 'pointerdown'].forEach(function (t) {
			window.addEventListener(t, function () { touched = true; }, { passive: true, capture: true });
		});

		function openLeadPanel() {
			var panel = document.getElementById('lead-panel');
			if (!panel || typeof window.leadOpen !== 'function') return;
			var was = panel.style.transition;
			panel.style.transition = 'none';
			window.leadOpen();
			void panel.offsetHeight;                                 // apply the open state before the transition returns
			panel.style.transition = was;
		}
		function land() {
			var margin = parseFloat(getComputedStyle(target).scrollMarginTop);
			var offset = margin > 0 ? margin : 69;                   // 69 = header height, same as the .scrollto handler
			var y = Math.max(0, Math.round(target.getBoundingClientRect().top + window.pageYOffset - offset));
			if (Math.abs(window.pageYOffset - y) > 1) window.scrollTo({ top: y, left: 0, behavior: 'instant' });
		}
		function sponsorJump() {
			openLeadPanel();
			land();
			touched = false;
			setTimeout(function () { if (!touched) land(); }, 300);
		}
		function closeMenu() {
			if (typeof closeMobileNav === 'function') return closeMobileNav();
			var nav = document.getElementById('navigation');
			if (nav && nav.classList.contains('show')) nav.classList.remove('show');
		}

		document.querySelectorAll('a[href="' + hash + '"]').forEach(function (a) {
			a.addEventListener('click', function (e) {
				e.preventDefault();
				e.stopImmediatePropagation();                        // not the smooth .scrollto handler, not the animated leadOpen
				if (history.pushState) history.pushState(null, '', hash);
				sponsorJump();
				closeMenu();
			}, true);
		});

		// arriving with the hash (talk page hero button -> ./#sponsors, navbar from a subpage, a shared link)
		if (location.hash === hash) {
			var arrive = function () { if (!touched) { openLeadPanel(); land(); } };
			if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrive); else arrive();
			window.addEventListener('load', arrive);
		}
	} catch (e) {}
})();


/* ===== Smooth scrolling ====== */
/*  Note: You need to include smoothscroll.min.js (smooth scroll behavior polyfill) on the page to cover some browsers */
/* Ref: https://github.com/iamdustan/smoothscroll */


let scrollLinks = document.querySelectorAll('.scrollto');
const pageNavWrapper = document.getElementById('navigation');

scrollLinks.forEach((scrollLink) => {

	scrollLink.addEventListener('click', (e) => {
		
		e.preventDefault();

		let element = document.querySelector(scrollLink.getAttribute("href"));
		
		const yOffset = 69; //page .header height
		
		//console.log(yOffset);
		
		const y = element.getBoundingClientRect().top + window.pageYOffset - yOffset;
		
		window.scrollTo({top: y, behavior: 'smooth'});
		history.pushState(null, null, scrollLink.getAttribute("href"));
		
		
		//Collapse mobile menu after clicking
		closeMobileNav();

		
    });
	
});
    

/* ===== Mobile menu ===== */
/* Collapse the open hamburger menu. Used by the .scrollto handler above and by a delegated
   handler below, so plain nav-link anchors (sreday home, all event pages) close it too. */
function closeMobileNav() {
	if (!pageNavWrapper || !pageNavWrapper.classList.contains('show')) return;
	if (window.bootstrap && bootstrap.Collapse) {
		bootstrap.Collapse.getOrCreateInstance(pageNavWrapper, { toggle: false }).hide();
	} else {
		pageNavWrapper.classList.remove('show');
	}
}
if (pageNavWrapper) {
	pageNavWrapper.addEventListener('click', (e) => { if (e.target.closest('a')) closeMobileNav(); });
}

/* ===== Navbar glow ===== */
// A soft vertical flare behind the glassy navbar that follows the mouse (styles: "NAVBAR GLOW" in the css).
// Desktop pointers only; the beam eases towards the cursor so it trails a little. Never throws.
(function () {
	try {
		var bar = document.getElementById('header') || document.querySelector('.navbar.fixed-top');
		if (!bar || !window.matchMedia || !window.requestAnimationFrame) return;
		if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
		var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		var wrap = document.createElement('div'), beam = document.createElement('i');
		wrap.className = 'nav-glow'; wrap.setAttribute('aria-hidden', 'true'); wrap.appendChild(beam);
		bar.insertBefore(wrap, bar.firstChild);
		var x = null, target = 0, raf = 0;
		function frame() {
			raf = 0;
			x += (target - x) * 0.16;
			if (Math.abs(target - x) < 0.4) x = target;
			beam.style.transform = 'translate3d(' + x.toFixed(1) + 'px,0,0)';
			if (x !== target) raf = window.requestAnimationFrame(frame);
		}
		bar.addEventListener('mousemove', function (e) {
			target = e.clientX - bar.getBoundingClientRect().left;
			if (x === null || still) x = target;
			wrap.classList.add('on');
			if (!raf) raf = window.requestAnimationFrame(frame);
		});
		bar.addEventListener('mouseleave', function () { wrap.classList.remove('on'); });
	} catch (e) {}
})();

/* ===== Gumshoe SrollSpy ===== */
/* Ref: https://github.com/cferdinandi/gumshoe  */
// Get the sticky header


// Initialize Gumshoe
var spy = new Gumshoe('#navigation a', {
	offset: 69 //page .header heights
});


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

